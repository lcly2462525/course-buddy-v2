import os
from pathlib import Path
from typing import Any, Dict

import yaml
from dotenv import load_dotenv


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    return value


def _resolve_path(base_dir: Path, value: str) -> str:
    expanded = Path(os.path.expanduser(value))
    if expanded.is_absolute():
        return str(expanded)
    return str((base_dir / expanded).resolve())


DEFAULT_CONFIG = {
    "root_dir": "data",
    "cookies_path": "~/.config/canvas/cookies.json",
    "cookies_from_browser": "auto",
    "courses": {},
    "llm": {
        "enabled": True,
        "api_key": "",
        "api_key_env": "LLM_API_KEY",
        "base_url": "https://aihubmix.com/v1",
        "model": "qwen3-max",
        "temperature": 0.3,
        "use_env_proxy": True,
        "request_timeout": 600,
        "retries": 3,
        "notes_chunk_minutes": 12,
        "notes_chunk_max_chars": 12000,
        "notes_chunk_output_tokens": 3200,
        "notes_merge_max_tokens": 12000,
        "providers": {},
    },
}


def ensure_config_file(config_path: Path) -> bool:
    """config.yaml 不存在时用内置默认自动创建。返回是否新建。"""
    if config_path.exists():
        return False
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(DEFAULT_CONFIG, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    return True


def load_config(path: str) -> Dict[str, Any]:
    config_path = Path(path).expanduser().resolve()
    # 缺文件不再崩溃：自动生成一份默认配置。
    ensure_config_file(config_path)
    load_dotenv(config_path.parent / ".env", override=False)
    legacy_env = config_path.parent.parent / "course-buddy" / ".env"
    if legacy_env.exists():
        load_dotenv(legacy_env, override=False)
    with config_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    cfg = _expand_env(cfg)
    cfg["config_path"] = str(config_path)
    cfg["config_dir"] = str(config_path.parent)
    cfg["root_dir"] = _resolve_path(config_path.parent, cfg.get("root_dir", "data"))

    cookies_path = cfg.get("cookies_path")
    if cookies_path:
        cfg["cookies_path"] = _resolve_path(config_path.parent, cookies_path)
    return cfg
