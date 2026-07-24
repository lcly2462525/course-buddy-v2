import argparse
import glob
import json
import re
import sys
from pathlib import Path

import yaml
from rich.console import Console
from rich.prompt import Prompt, Confirm
from rich.table import Table

from .canvas_api import filter_real_courses, get_active_courses
from .config import load_config
from .notes import summarize_transcript_files
from .replay_api import (
    ensure_cookies,
    get_cookies_from_browser,
    get_video_list,
    get_video_platform_token,
    save_cookies,
    validate_cookies,
)
from .transcript_api import fetch_transcript_bundle, filter_replays_since

console = Console()


def _progress(message: str) -> None:
    console.print(f"[cyan]{message}[/cyan]")


def _default_config_path() -> str:
    return str(Path(__file__).resolve().parents[1] / "config.yaml")


def _load_courses():
    return filter_real_courses(get_active_courses())


def _course_data_dir(root_dir: str, course_id: str) -> Path:
    return Path(root_dir) / "downloads" / str(course_id)


def _infer_course_name_from_local_files(root_dir: str, course_id: str) -> str:
    course_root = _course_data_dir(root_dir, course_id)
    for subdir, pattern in (("notes", "*.md"), ("transcripts", "*.json"), ("platform_summaries", "*.json")):
        for path in sorted((course_root / subdir).glob(pattern), reverse=True):
            stem = path.stem
            if len(stem) > 11 and stem[:10].count("-") == 2 and stem[10] == "_":
                title = stem[11:]
                marker = title.find("(第")
                if marker > 0:
                    return title[:marker]
                if title:
                    return title
    return str(course_id)


def _parse_index_list(value: str) -> list[int]:
    try:
        return [int(v.strip()) for v in value.split(",") if v.strip()]
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"无效的索引值：{value!r}，请使用整数或逗号分隔的整数列表，如 12 或 12,13,14"
        )


def _normalize_course_arg(args) -> None:
    if not hasattr(args, "course"):
        return
    if not getattr(args, "course", None):
        args.course = getattr(args, "course_pos", None)
    if not getattr(args, "course", None):
        raise RuntimeError("请提供课程 ID，例如：cb all 87081 或 cb ask '帮我整理 87081 最新一讲'")


def _default_latest_when_no_selector(args) -> None:
    if (
        hasattr(args, "latest")
        and not args.latest
        and getattr(args, "index", None) is None
        and getattr(args, "cour_id", None) is None
    ):
        args.latest = True


def _namespace(**kwargs) -> argparse.Namespace:
    defaults = {
        "config": _default_config_path(),
        "course": None,
        "course_pos": None,
        "latest": False,
        "index": None,
        "cour_id": None,
        "since": None,
        "glob": None,
        "force": False,
        "model": None,
        "base_url": None,
        "api_key": None,
        "api_key_env": None,
        "no_llm": False,
        "head": 120,
        "full": False,
        "notes": False,
        "transcript": False,
        "summary": False,
        "txt": False,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


_CN_NUMBERS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def _parse_cn_number(value: str) -> int | None:
    value = value.strip()
    if value.isdigit():
        return int(value)
    if value in _CN_NUMBERS:
        return _CN_NUMBERS[value]
    if value.startswith("十") and len(value) == 2:
        return 10 + (_CN_NUMBERS.get(value[1]) or 0)
    if value.endswith("十") and len(value) == 2:
        return (_CN_NUMBERS.get(value[0]) or 0) * 10
    if "十" in value and len(value) == 3:
        left, right = value.split("十", 1)
        return (_CN_NUMBERS.get(left) or 0) * 10 + (_CN_NUMBERS.get(right) or 0)
    return None


def _parse_natural_since(text: str) -> str | None:
    direct = re.search(r"(\d+)\s*([dwm])", text, re.I)
    if direct:
        return f"{direct.group(1)}{direct.group(2).lower()}"

    match = re.search(r"最近\s*([一二两三四五六七八九十\d]+)?\s*(天|日|周|星期|月)", text)
    if not match:
        return None
    amount = _parse_cn_number(match.group(1) or "一") or 1
    unit = match.group(2)
    if unit in {"天", "日"}:
        return f"{amount}d"
    if unit in {"周", "星期"}:
        return f"{amount}w"
    return f"{amount}m"


def _parse_natural_index(text: str) -> list[int] | None:
    match = re.search(r"(?:索引|index)\s*([0-9,\s]+)", text, re.I)
    if match:
        return _parse_index_list(match.group(1))

    match = re.search(r"第\s*([一二两三四五六七八九十\d]+)\s*(?:个|条)(?:回放|视频|录像)?", text)
    if not match:
        return None
    number = _parse_cn_number(match.group(1))
    return [number] if number is not None else None


def _parse_natural_request(text: str, config: str) -> argparse.Namespace:
    course_match = re.search(r"(?<!\d)(\d{4,})(?!\d)", text)
    course = course_match.group(1) if course_match else None
    index = _parse_natural_index(text)
    latest = index is None and any(word in text for word in ("最新", "最近一讲", "上一讲", "最后一讲"))
    since = _parse_natural_since(text)
    force = any(word in text for word in ("覆盖", "重新", "重做", "再生成"))
    no_llm = any(word in text.lower() for word in ("no-llm", "不用llm", "不要llm", "不用模型", "免费", "平台摘要"))
    full = any(word in text for word in ("完整", "全文", "全部"))
    txt = any(word in text for word in ("原文", "纯文本", "txt", "转录文本"))
    transcript = any(word in text for word in ("json", "transcript"))
    summary = any(word in text for word in ("摘要", "summary"))
    model_match = re.search(r"(?:模型|model)\s*[:：]?\s*([A-Za-z0-9._/\-]+)", text)

    base = {
        "config": config,
        "course": course,
        "latest": latest,
        "index": index,
        "since": since,
        "force": force,
        "no_llm": no_llm,
        "full": full,
        "txt": txt,
        "transcript": transcript,
        "summary": summary,
        "model": model_match.group(1) if model_match else None,
    }

    if any(word in text for word in ("课程列表", "所有课程", "有哪些课", "列课程")):
        return _namespace(config=config, handler=cmd_list_courses)
    if any(word in text for word in ("回放列表", "视频列表", "录像列表", "有哪些回放", "列回放")) or (
        "回放" in text and any(word in text for word in ("列", "列表", "有哪些"))
    ):
        return _namespace(**base, handler=cmd_list_replays)
    if any(word in text for word in ("看", "查看", "看看", "读取", "读一下", "打开")) and any(
        word in text for word in ("笔记", "转录", "摘要", "原文", "结果")
    ):
        return _namespace(**base, handler=cmd_read)
    if any(word in text for word in ("下载", "抓取", "获取")) and not any(word in text for word in ("笔记", "整理", "总结")):
        return _namespace(**base, handler=cmd_fetch_transcript)
    if any(word in text for word in ("笔记", "整理", "总结", "复习")):
        base["latest"] = latest or index is None
        return _namespace(**base, handler=cmd_all)
    raise RuntimeError(
        "还没听懂这句话。可以试试：cb ask '帮我整理 87081 最新一讲'，或 cb ask '看 87081 最新笔记'"
    )


def _pick_latest_file(paths: list[str]) -> str:
    if not paths:
        raise RuntimeError("没有找到匹配文件。")
    return sorted(paths)[-1]


def _read_text_file(path: Path, *, head: int | None, full: bool) -> str:
    text = path.read_text(encoding="utf-8")
    if full:
        return text
    lines = text.splitlines()
    if head is None:
        head = 120
    return "\n".join(lines[:head])


def _find_read_target(root: Path, kind: str, glob_pattern: str | None, latest: bool, index: int | None) -> tuple[Path, str]:
    candidates = []
    if kind == "notes":
        candidates = [
            ("notes", root / "notes", glob_pattern or "*.md"),
            ("txt", root / "transcripts", "*.txt"),
            ("transcript", root / "transcripts", "*.json"),
            ("summary", root / "platform_summaries", "*.json"),
        ]
    elif kind == "summary":
        candidates = [("summary", root / "platform_summaries", glob_pattern or "*.json")]
    elif kind == "txt":
        candidates = [("txt", root / "transcripts", glob_pattern or "*.txt")]
    elif kind == "transcript":
        candidates = [("transcript", root / "transcripts", glob_pattern or "*.json")]

    for resolved_kind, subdir, pattern in candidates:
        matches = sorted(glob.glob(str(subdir / pattern)))
        if not matches:
            continue
        if latest or index is None:
            return Path(_pick_latest_file(matches)), resolved_kind
        return Path(matches[index]), resolved_kind
    raise RuntimeError("没有找到匹配文件。")


def cmd_list_courses(args) -> int:
    courses = _load_courses()
    try:
        _, data = _load_raw_config(args.config)
        aliases = data.get("courses") or {}
    except Exception:
        aliases = {}
    id_to_alias: dict[str, list[str]] = {}
    for alias, cid in aliases.items():
        id_to_alias.setdefault(str(cid), []).append(str(alias))

    table = Table(title="Canvas 课程")
    table.add_column("ID")
    table.add_column("别名")
    table.add_column("课程名")
    table.add_column("课程代码")
    table.add_column("学期")
    for course in courses:
        table.add_row(
            str(course["id"]),
            ", ".join(id_to_alias.get(str(course["id"]), [])),
            course.get("name") or "",
            course.get("course_code") or "",
            (course.get("term") or {}).get("name") or "",
        )
    console.print(table)
    return 0


def cmd_list_replays(args) -> int:
    cfg = load_config(args.config)
    course_name = _infer_course_name_from_local_files(cfg["root_dir"], str(args.course))
    _progress("正在检查 Canvas 登录态...")
    cookies = ensure_cookies(cfg.get("cookies_path"), cfg.get("cookies_from_browser", "auto"))
    _progress("正在获取课程回放列表...")
    token, canvas_course_id, session = get_video_platform_token(str(args.course), cookies)
    videos = filter_replays_since(get_video_list(token, canvas_course_id, session), args.since)

    table = Table(title=f"回放列表 · {course_name}")
    table.add_column("Index")
    table.add_column("标题")
    table.add_column("开始时间")
    table.add_column("结束时间")
    table.add_column("courId")
    for index, video in enumerate(reversed(videos)):
        table.add_row(
            str(index),
            video.get("videoName") or "",
            video.get("courseBeginTime") or "",
            video.get("courseEndTime") or "",
            str(video.get("courId") or ""),
        )
    console.print(table)
    return 0


def _fetch_transcript_results(args, cfg: dict, course_name: str) -> list[dict]:
    _progress("正在检查 Canvas 登录态...")
    cookies = ensure_cookies(cfg.get("cookies_path"), cfg.get("cookies_from_browser", "auto"))
    indices = args.index if args.index else [None]
    results = []
    for index in indices:
        result = fetch_transcript_bundle(
            course_id=str(args.course),
            course_name=course_name,
            oc_cookies=cookies,
            root_dir=cfg["root_dir"],
            latest=args.latest,
            index=index,
            cour_id=args.cour_id,
            since=args.since,
            progress=_progress,
        )
        results.append(result)
    return results


def _print_fetch_result(result: dict, course_name: str) -> None:
    replay = result["replay"]
    detail = result["detail"]
    duration_seconds = detail.get("videPlayTime") or 0
    transcript_seconds = result.get("transcript_seconds") or 0
    console.print(f"[green]已下载[/green] {replay.get('videoName')}")
    console.print(f"课程: {course_name}")
    console.print(f"回放开始: {replay.get('courseBeginTime')}")
    console.print(f"视频时长: {duration_seconds / 60:.2f} 分钟")
    console.print(f"转录覆盖: {transcript_seconds / 60:.2f} 分钟")
    console.print(f"分段数: {result['segments']}")
    console.print(f"Transcript JSON: {result['transcript_path']}")
    console.print(f"Transcript TXT: {result['transcript_txt_path']}")
    console.print(f"Platform Summary: {result['summary_path']}")


def cmd_fetch_transcript(args) -> int:
    cfg = load_config(args.config)
    course_name = _infer_course_name_from_local_files(cfg["root_dir"], str(args.course))
    for result in _fetch_transcript_results(args, cfg, course_name):
        _print_fetch_result(result, course_name)
    return 0


def _resolve_note_sources(root_dir: str, course_id: str, glob_pattern: str | None) -> list[tuple[str, str]]:
    transcript_dir = Path(root_dir) / "downloads" / str(course_id) / "transcripts"
    summary_dir = Path(root_dir) / "downloads" / str(course_id) / "platform_summaries"
    pattern = glob_pattern or "*.json"
    transcript_files = sorted(glob.glob(str(transcript_dir / pattern)))
    pairs = []
    for transcript_path in transcript_files:
        summary_path = summary_dir / Path(transcript_path).name
        if summary_path.exists():
            pairs.append((transcript_path, str(summary_path)))
    return pairs


def _build_llm_cfg(cfg: dict, args) -> dict:
    llm_cfg = dict(cfg.get("llm", {}))
    if getattr(args, "no_llm", False):
        llm_cfg["enabled"] = False
    if getattr(args, "model", None):
        llm_cfg["model"] = args.model
    if getattr(args, "base_url", None):
        llm_cfg["base_url"] = args.base_url
    if getattr(args, "api_key", None):
        llm_cfg["api_key"] = args.api_key
    if getattr(args, "api_key_env", None):
        llm_cfg["api_key_env"] = args.api_key_env
    return llm_cfg


def cmd_notes(args) -> int:
    cfg = load_config(args.config)
    notes_dir = Path(cfg["root_dir"]) / "downloads" / str(args.course) / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)

    pairs = _resolve_note_sources(cfg["root_dir"], str(args.course), args.glob)
    course_name = _infer_course_name_from_local_files(cfg["root_dir"], str(args.course))
    if not pairs and (args.latest or args.index is not None or args.cour_id is not None):
        _progress("本地没有匹配的 transcript，先去抓取回放文本...")
        cookies = ensure_cookies(cfg.get("cookies_path"), cfg.get("cookies_from_browser", "auto"))
        for index in (args.index if args.index else [None]):
            fetch_transcript_bundle(
                course_id=str(args.course),
                course_name=course_name,
                oc_cookies=cookies,
                root_dir=cfg["root_dir"],
                latest=args.latest,
                index=index,
                cour_id=args.cour_id,
                since=args.since,
                progress=_progress,
            )
        pairs = _resolve_note_sources(cfg["root_dir"], str(args.course), args.glob)

    if not pairs:
        raise RuntimeError("没有找到可用于生成笔记的 transcript/summary 文件。")
    _progress(f"共找到 {len(pairs)} 份可处理的 transcript。")

    llm_cfg = _build_llm_cfg(cfg, args)

    generated = 0
    for idx, (transcript_path, summary_path) in enumerate(pairs, start=1):
        out_path = notes_dir / (Path(transcript_path).stem + ".md")
        if out_path.exists() and not args.force:
            console.print(f"[dim]跳过已有笔记: {out_path.name}[/dim]")
            continue
        _progress(f"正在生成笔记 {idx}/{len(pairs)}：{Path(transcript_path).name}")
        md = summarize_transcript_files(
            transcript_path=transcript_path,
            summary_path=summary_path,
            course_name=course_name,
            llm_cfg=llm_cfg,
            progress=_progress,
        )
        out_path.write_text(md, encoding="utf-8")
        console.print(f"[green]笔记已写入[/green] {out_path}")
        generated += 1
    _progress(f"笔记阶段结束，共生成 {generated} 份笔记。")
    return 0


def cmd_all(args) -> int:
    _progress("开始执行 all：先抓 transcript，再生成笔记。")
    cfg = load_config(args.config)
    course_name = _infer_course_name_from_local_files(cfg["root_dir"], str(args.course))
    fetched = _fetch_transcript_results(args, cfg, course_name)
    result = 0
    for item in fetched:
        _print_fetch_result(item, course_name)
        notes_args = argparse.Namespace(**vars(args))
        notes_args.glob = Path(item["transcript_path"]).name
        result = cmd_notes(notes_args)
    _progress("all 执行完成。")
    return result


def cmd_setup(args) -> int:
    """交互式初始化配置向导"""
    config_path = Path(args.config).expanduser().resolve()
    config_dir = config_path.parent

    console.print("[bold cyan]欢迎使用 course-buddy-v2 配置向导[/bold cyan]\n")

    # 选择 LLM 提供商
    console.print("[yellow]第一步：选择 LLM 提供商[/yellow]")
    console.print("请选择一个提供商（或输入 custom 自定义）：\n")
    providers = [
        ("1", "aihubmix（默认，推荐）", "https://aihubmix.com/v1", "LLM_API_KEY"),
        ("2", "OpenAI", "https://api.openai.com/v1", "OPENAI_API_KEY"),
        ("3", "DeepSeek", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
        ("4", "阿里云通义（Qwen）", "https://dashscope.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY"),
        ("5", "硅基流动（SiliconFlow）", "https://api.siliconflow.cn/v1", "SILICONFLOW_API_KEY"),
        ("6", "自定义", None, None),
    ]
    for code, name, _, _ in providers:
        console.print(f"  {code}. {name}")
    
    choice = Prompt.ask("\n请选择 (1-6)", choices=["1", "2", "3", "4", "5", "6"])
    
    selected = next((p for p in providers if p[0] == choice), None)
    if not selected:
        console.print("[red]无效选择[/red]")
        return 1
    
    _, provider_name, base_url, api_key_env = selected
    
    if choice == "6":
        # 自定义提供商
        console.print("\n[yellow]自定义 LLM 提供商[/yellow]")
        base_url = Prompt.ask("请输入 API 基础地址", default="https://example.com/v1")
        api_key = Prompt.ask("请输入 API Key（或留空，稍后在环境变量中设置）", default="", password=True)
        api_key_env = "LLM_API_KEY"
        provider_name = "custom"
    else:
        # 内置提供商
        model_hint = {
            "1": "qwen-turbo",
            "2": "gpt-4o",
            "3": "deepseek-chat",
            "4": "qwen-max",
            "5": "Qwen3-235B-A22B",
        }.get(choice, "")
        api_key = Prompt.ask(f"\n请输入 {provider_name} 的 API Key（或留空，稍后在环境变量中设置）", default="", password=True)
    
    # 选择模型
    console.print("\n[yellow]第二步：选择模型[/yellow]")
    if choice == "6":
        model = Prompt.ask("请输入模型名称", default="your-model")
    else:
        model_hint = {
            "1": "qwen-turbo",
            "2": "gpt-4o",
            "3": "deepseek-chat",
            "4": "qwen-max",
            "5": "Qwen3-235B-A22B",
        }.get(choice, "")
        model = Prompt.ask(f"请输入模型名称", default=model_hint)
    
    # 其他配置
    console.print("\n[yellow]第三步：其他设置[/yellow]")
    use_proxy = Confirm.ask("是否使用环境变量中的代理（HTTP_PROXY/HTTPS_PROXY）？", default=True)
    
    # 构建配置
    config = {
        "root_dir": "data",
        "cookies_path": "~/.config/canvas/cookies.json",
        "cookies_from_browser": "auto",
        "courses": {},
        "llm": {
            "enabled": True,
            "api_key": api_key if api_key else "",
            "api_key_env": api_key_env,
            "base_url": base_url,
            "model": model,
            "temperature": 0.3,
            "use_env_proxy": use_proxy,
            "request_timeout": 600,
            "retries": 3,
            "notes_chunk_minutes": 12,
            "notes_chunk_max_chars": 12000,
            "notes_chunk_output_tokens": 2200,
            "notes_merge_max_tokens": 8000,
            "providers": {},
        },
    }
    
    # 保存配置
    config_dir.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    
    console.print(f"\n[green]✓ 配置已保存到：{config_path}[/green]")
    
    if not api_key:
        console.print(f"[yellow]⚠️  提醒：你选择了在环境变量中设置 API Key[/yellow]")
        console.print(f"   请在终端执行：[bold]export {api_key_env}=你的APIKey[/bold]")
    
    console.print("\n[cyan]下一步，请准备：[/cyan]")
    console.print("  1. Canvas API Token → ~/.config/canvas/token")
    console.print("  2. 浏览器 Cookie → 工具自动读取或手动配置")
    console.print("\n之后就可以开始使用了：[bold]cb list[/bold]")
    
    return 0


def _load_raw_config(config_path: str):
    """读原始 yaml（不做 load_config 的路径展开），用于就地编辑 config.yaml。"""
    path = Path(config_path).expanduser().resolve()
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}
    return path, data


def _cookies_from_json(data):
    if isinstance(data, list):  # EditThisCookie 导出的是 [{name, value, ...}]
        return {c["name"]: c.get("value", "") for c in data if isinstance(c, dict) and "name" in c}
    if isinstance(data, dict):
        if data.get("_format") == "session_cookies":
            return data.get("cookies", {})
        return data
    raise RuntimeError("cookie JSON 结构无法识别。")


def _parse_cookie_source(raw: str) -> dict:
    raw = raw.strip()
    candidate = Path(raw).expanduser()
    if candidate.exists():
        return _cookies_from_json(json.loads(candidate.read_text(encoding="utf-8")))
    # 当作 "k=v; k2=v2" 的 Cookie 请求头字符串
    if "=" in raw:
        cookies = {}
        for part in raw.split(";"):
            if "=" in part:
                key, value = part.split("=", 1)
                cookies[key.strip()] = value.strip()
        if cookies:
            return cookies
    raise RuntimeError("无法识别的 cookie 来源：既不是存在的文件，也不是 'k=v; ...' 字符串。")


def cmd_login(args) -> int:
    """设置/刷新 oc.sjtu.edu.cn 登录 cookie。"""
    cfg = load_config(args.config)
    cookie_path = cfg.get("cookies_path")
    source = getattr(args, "source", None)

    if source:
        cookies = _parse_cookie_source(source)
        if not validate_cookies(cookies):
            console.print("[yellow]提示：这份 cookie 未通过校验（可能已过期），仍为你保存，可先试试。[/yellow]")
        save_cookies(cookies, cookie_path)
        console.print(f"[green]已保存 cookie 到[/green] {cookie_path}")
        return 0

    # 自动读取默认关闭：它会调 browser_cookie3，在 macOS 上可能触发钥匙串弹窗甚至卡住。
    # 想试自动读取用 cb login --auto；否则默认给稳妥的手动导入引导。
    if getattr(args, "auto", False):
        _progress("尝试从浏览器自动读取 cookie（若弹出钥匙串授权请允许）...")
        cookies = get_cookies_from_browser(cfg.get("cookies_from_browser", "auto"))
        if cookies and validate_cookies(cookies):
            save_cookies(cookies, cookie_path)
            console.print(f"[green]自动读取成功，已保存到[/green] {cookie_path}")
            return 0
        console.print("[red]自动读取失败。[/red] 改用下面的手动导入。")

    console.print("手动导入登录态（任选一种，都很快）：")
    console.print("  A. 用扩展导出文件：")
    console.print("     1) 浏览器登录 https://oc.sjtu.edu.cn")
    console.print("     2) 装 EditThisCookie 扩展，在该站点页面点它 → Export")
    console.print("     3) 存成文件后运行：[bold]cb login ~/cookies.json[/bold]")
    console.print("  B. 直接粘贴 Cookie 请求头：")
    console.print("     [bold]cb login '_normandy_session=xxx; log_session_id=yyy'[/bold]")
    console.print("  C. 想试自动读取（可能弹钥匙串授权）：[bold]cb login --auto[/bold]")
    return 0


def cmd_alias(args) -> int:
    """管理课程别名：cb alias 数值 88918 / cb alias（列出）/ cb alias 数值 --del。"""
    path, data = _load_raw_config(args.config)
    courses = data.get("courses") or {}

    if not args.name:
        if not courses:
            console.print("还没有别名。用法：[bold]cb alias 数值 88918[/bold]，之后就能 [bold]cb 数值[/bold]。")
            return 0
        table = Table(title="课程别名")
        table.add_column("别名")
        table.add_column("课程 ID")
        for key, value in courses.items():
            table.add_row(str(key), str(value))
        console.print(table)
        return 0

    if getattr(args, "delete", False):
        if courses.pop(args.name, None) is None:
            console.print(f"[yellow]没有名为 {args.name} 的别名。[/yellow]")
        else:
            console.print(f"[green]已删除别名 {args.name}。[/green]")
    else:
        if not args.course_id:
            raise RuntimeError("用法：cb alias <别名> <课程ID>；或 cb alias <别名> --del 删除。")
        courses[args.name] = str(args.course_id)
        console.print(f"[green]已设置别名[/green] {args.name} → {args.course_id}")

    data["courses"] = courses
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    return 0


def _resolve_course_alias(args) -> None:
    """把非数字的 course 参数当作别名，解析成课程 ID。"""
    course = getattr(args, "course", None)
    if not course:
        return
    course = str(course)
    if course.isdigit():
        return
    try:
        _, data = _load_raw_config(args.config)
    except Exception:
        return
    courses = data.get("courses") or {}
    for key, value in courses.items():
        if str(key).lower() == course.lower():
            args.course = str(value)
            return
    hits = [(k, v) for k, v in courses.items() if course.lower() in str(k).lower()]
    if len(hits) == 1:
        args.course = str(hits[0][1])
        return
    if len(hits) > 1:
        names = "，".join(str(k) for k, _ in hits)
        raise RuntimeError(f"别名 “{course}” 不唯一（匹配到：{names}）。请更精确，或直接用课程 ID。")
    raise RuntimeError(f"不认识课程 “{course}”。用 cb list 看课程，或先 cb alias {course} <课程ID> 建个别名。")


def cmd_read(args) -> int:
    cfg = load_config(args.config)
    root = _course_data_dir(cfg["root_dir"], str(args.course))

    kind = "notes"
    if args.transcript:
        kind = "transcript"
    elif args.summary:
        kind = "summary"
    elif args.txt:
        kind = "txt"

    _progress(f"正在查找 {kind} 文件...")
    target, resolved_kind = _find_read_target(root, kind, args.glob, args.latest or not args.glob, args.index)
    if resolved_kind != kind:
        console.print(f"[yellow]未找到 {kind}，已自动切换为 {resolved_kind}。[/yellow]")

    console.print(f"[green]文件[/green] {target}")
    content = _read_text_file(target, head=args.head, full=args.full)
    # markup=False：笔记里的数学 `[a,b]` 等方括号不会被 rich 当成样式标签吃掉。
    console.print(content, markup=False)
    if not args.full:
        console.print("[dim]已输出文件前半部分；如需完整内容，追加 --full。[/dim]")
    return 0


_MANUAL_SECTIONS = [
    ("最常用", [
        ("cb 数值", "整理这门课最新一讲（下载转录 + 生成笔记）"),
        ("cb read 数值", "查看刚生成的笔记"),
        ("cb all 数值 -i 3", "整理指定第 3 讲（0 是最新）"),
        ("cb notes 数值 -l --free", "不用 LLM，只出平台摘要（免费快速）"),
    ]),
    ("认证与配置", [
        ("cb login", "登录态失效时，按提示导入 cookie（最稳）"),
        ("cb login ~/cookies.json", "用 EditThisCookie 导出的 JSON 导入"),
        ("cb alias 数值 88918", "给课程起别名，之后哪都能用「数值」"),
        ("cb setup", "交互式配置 LLM（也可直接编辑 .env / config.yaml）"),
    ]),
    ("查看与列举", [
        ("cb list", "列出本学期所有课程（含别名）"),
        ("cb videos 数值 -s 2w", "看某课回放列表（最近两周）"),
        ("cb read 数值 --full", "看完整笔记"),
        ("cb read 数值 --txt", "看转录原文"),
    ]),
    ("分步（进阶）", [
        ("cb fetch 数值 -l", "只下载转录，不生成笔记（忠实转录）"),
        ("cb notes 数值 -l", "从已下载的转录生成笔记"),
        ("cb notes 数值 -l -m deepseek/deepseek-chat", "换模型"),
        ("cb ask '整理 数值 最新一讲'", "用一句自然语言描述要做的事"),
    ]),
]


def _print_manual() -> int:
    console.print("[bold cyan]course-buddy · 实用手册[/bold cyan]  从 Canvas 回放抓转录、生成笔记")
    console.print("[dim]先 cb alias 起别名，之后用「数值」代替课程 ID。加 -h 看某命令详细参数，如 cb all -h。[/dim]\n")
    for title, rows in _MANUAL_SECTIONS:
        table = Table(show_header=False, box=None, padding=(0, 2, 0, 0), title=f"[bold]{title}[/bold]", title_justify="left")
        table.add_column(style="green", no_wrap=True)
        table.add_column(style="white")
        for cmd, desc in rows:
            table.add_row(cmd, desc)
        console.print(table)
        console.print()
    return 0


def cmd_help(args) -> int:
    return _print_manual()


def cmd_ask(args) -> int:
    text = " ".join(args.text).strip()
    if not text:
        raise RuntimeError("请写一句你想做什么，例如：cb ask '帮我整理 87081 最新一讲'")
    parsed = _parse_natural_request(text, args.config)
    if parsed.handler is not cmd_list_courses:
        _normalize_course_arg(parsed)
    if parsed.handler in {cmd_fetch_transcript, cmd_all}:
        _default_latest_when_no_selector(parsed)
    return parsed.handler(parsed)


def _add_course_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("course_pos", nargs="?", help="Canvas 课程 ID，可直接写在命令后")
    parser.add_argument("-c", "--course", help="Canvas 课程 ID")


def _add_replay_selector_args(parser: argparse.ArgumentParser, *, latest_help: str, index_help: str) -> None:
    parser.add_argument("-l", "--latest", action="store_true", help=latest_help)
    parser.add_argument("-i", "--index", type=_parse_index_list, help=index_help)
    parser.add_argument("--cour-id", type=int, help="按平台 courId 选择")
    parser.add_argument("-s", "--since", default=None, help="只在最近时间范围内选择，如 7d/2w/1m")


def _add_llm_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-m", "--model", default=None, help="指定 LLM 模型，支持 provider/model 格式")
    parser.add_argument("--base-url", default=None, help="自定义 LLM API 基础地址，例如 https://example.com/v1")
    parser.add_argument("--api-key", default=None, help="自定义 LLM API Key")
    parser.add_argument("--api-key-env", default=None, help="从指定环境变量读取 API Key")
    parser.add_argument("--no-llm", "--free", action="store_true", help="不调用模型，直接输出平台摘要和转录摘录")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cb",
        description="SJTU Canvas replay transcript helper",
        epilog=(
            "快捷示例：cb all 87081 | cb n 87081 -l | "
            "cb r 87081 --summary | cb ask '帮我整理 87081 最新一讲'"
        ),
    )
    parser.add_argument("--config", default=_default_config_path(), help="配置文件路径")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("help", aliases=["手册", "帮助"], help="显示实用手册（也可直接输入 cb）").set_defaults(handler=cmd_help)

    sub.add_parser("setup", help="交互式配置向导").set_defaults(handler=cmd_setup)

    p = sub.add_parser("login", help="设置/刷新 oc.sjtu.edu.cn 登录 cookie")
    p.add_argument("source", nargs="?", help="cookie 文件路径 或 'k=v; k2=v2' 字符串；省略则给手动导入引导")
    p.add_argument("--auto", action="store_true", help="尝试从浏览器自动读取（可能弹钥匙串授权）")
    p.set_defaults(handler=cmd_login)

    p = sub.add_parser("alias", help="管理课程别名，如 cb alias 数值 88918")
    p.add_argument("name", nargs="?", help="别名")
    p.add_argument("course_id", nargs="?", help="课程 ID")
    p.add_argument("--del", "--delete", dest="delete", action="store_true", help="删除该别名")
    p.set_defaults(handler=cmd_alias)

    sub.add_parser("list-courses", aliases=["list", "ls", "courses"], help="列出当前学期 Canvas 课程").set_defaults(
        handler=cmd_list_courses
    )

    p = sub.add_parser("list-replays", aliases=["list-videos", "videos", "replays", "v"], help="列出课程回放")
    _add_course_arg(p)
    p.add_argument("-s", "--since", default=None, help="仅列出最近时间范围内回放，如 7d/2w/1m")
    p.set_defaults(handler=cmd_list_replays)

    p = sub.add_parser("fetch-transcript", aliases=["fetch", "get", "download", "f"], help="下载 transcript 和平台 summary")
    _add_course_arg(p)
    _add_replay_selector_args(p, latest_help="下载最新一讲", index_help="按回放索引下载，支持逗号分隔多个，如 12 或 12,13,14")
    p.set_defaults(handler=cmd_fetch_transcript)

    p = sub.add_parser("notes", aliases=["note", "n"], help="从 transcript 生成笔记")
    _add_course_arg(p)
    p.add_argument("-g", "--glob", default=None, help="transcript 文件匹配模式")
    _add_replay_selector_args(p, latest_help="若本地没有 transcript，先抓最新一讲", index_help="若本地没有 transcript，先抓指定索引，支持逗号分隔多个")
    p.add_argument("-f", "--force", action="store_true", help="覆盖已有笔记")
    _add_llm_args(p)
    p.set_defaults(handler=cmd_notes)

    p = sub.add_parser("all", aliases=["a", "latest"], help="下载 transcript 后直接生成笔记")
    _add_course_arg(p)
    _add_replay_selector_args(p, latest_help="处理最新一讲", index_help="处理指定索引，支持逗号分隔多个，如 12 或 12,13,14")
    p.add_argument("-g", "--glob", default=None, help="生成笔记时使用的 transcript 匹配模式")
    p.add_argument("-f", "--force", action="store_true", help="覆盖已有 transcript 文本和笔记")
    _add_llm_args(p)
    p.set_defaults(handler=cmd_all)

    p = sub.add_parser("read", aliases=["show", "r"], help="快速查看已生成的结果文件")
    _add_course_arg(p)
    p.add_argument("-g", "--glob", default=None, help="文件匹配模式")
    p.add_argument("-l", "--latest", action="store_true", help="读取最新文件")
    p.add_argument("-i", "--index", type=int, help="读取匹配结果中的指定索引")
    p.add_argument("--head", type=int, default=120, help="默认只输出前多少行")
    p.add_argument("--full", action="store_true", help="输出完整文件内容")
    p.add_argument("--notes", action="store_true", help="读取笔记 Markdown（默认）")
    p.add_argument("--transcript", action="store_true", help="读取 transcript JSON")
    p.add_argument("--summary", action="store_true", help="读取平台 summary JSON")
    p.add_argument("--txt", action="store_true", help="读取 transcript 纯文本")
    p.set_defaults(handler=cmd_read)

    p = sub.add_parser("ask", aliases=["do"], help="用一句自然语言描述要做的事")
    p.add_argument("text", nargs="+", help="例如：帮我整理 87081 最新一讲")
    p.set_defaults(handler=cmd_ask)

    return parser


def _known_commands(parser: argparse.ArgumentParser) -> set[str]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices.keys())
    return set()


def _inject_default_command(argv: list[str], known: set[str]) -> list[str]:
    """让 `cb 数值` / `cb 88918` 等价于 `cb all <课程> --latest`。
    第一个 token 若不是选项、也不是已知子命令，就默认补上 all。"""
    if not argv:
        return argv
    first = argv[0]
    if first.startswith("-") or first in known:
        return argv
    return ["all"] + argv


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    # 直接输入 cb（无参数）→ 显示实用手册，而不是 argparse 报错。
    if not argv:
        return _print_manual()
    parser = build_parser()
    argv = _inject_default_command(argv, _known_commands(parser))
    args = parser.parse_args(argv)
    try:
        _normalize_course_arg(args)
        _resolve_course_alias(args)
        if args.handler in {cmd_fetch_transcript, cmd_all}:
            _default_latest_when_no_selector(args)
        return args.handler(args)
    except Exception as exc:
        console.print(f"[red]{exc}[/red]")
        return 1


if __name__ == "__main__":
    sys.exit(main())
