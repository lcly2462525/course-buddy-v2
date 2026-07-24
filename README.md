# course-buddy-v2

把交大 Canvas 上的**课堂录音文字**，自动整理成一份干净的 Markdown 笔记。

- 它**不**下载视频，也**不**自己做语音识别。
- 它抓平台**已经生成好的转录文字**，再交给 AI 整理成结构化笔记（含公式、推导、重点）。
- 你在终端敲一条命令，几分钟后就能拿到一份 `.md` 笔记。

> 看不懂命令没关系。**直接输入 `cb`**（不带任何参数）就会弹出一页实用手册，照着敲即可。

---

## 一分钟看懂它怎么工作

```
你的一条命令  →  登录 Canvas  →  抓这门课的转录文字  →  AI 整理  →  笔记.md 存到本地
```

三样东西它需要你准备一次——**全部由 `cb setup` 一条命令引导完成**，你只管粘贴：

| 需要什么 | 干嘛用的 |
|---|---|
| **AI 的 API Key** | 让它能把转录整理成笔记 |
| **Canvas Token** | 让它能列出你的课 |
| **登录 Cookie** | 让它能进回放平台拿转录 |

> 只想要**忠实的转录文字**、自己再喂给别的 AI？那 API Key 可以先跳过，用 `cb fetch` 就行。

---

## 第一步：安装（只做一次）

需要电脑上有 Python 3.10 或更新版本。在项目文件夹里打开终端：

```bash
# 1) 建一个独立环境（避免弄乱系统 Python）
python3 -m venv .venv
source .venv/bin/activate        # Windows 用：.venv\Scripts\activate

# 2) 安装
pip install -e .
```

装好后，终端里就有 `cb` 命令了。试着敲一下：

```bash
cb
```

能看到一页命令手册，就说明装成功了。

> 之后每次新开终端，要先 `source .venv/bin/activate` 再用 `cb`。

---

## 第二步：一条命令配好（`cb setup`）

配置全交给向导，你只管跟着填：

```bash
cb setup
```

它会依次问你三样东西，**每样都能跳过、也能保持已有的不动**：

1. **AI Key** —— 选提供商、粘贴 key（自动存进 `.env`，不进 git）
2. **Canvas Token** —— 粘贴令牌（自动存好）
3. **登录态 Cookie** —— 选文件导入 / 粘贴 / 自动读取 / 跳过

> config.yaml 不用手动建——缺了会自动生成。

配完随时体检，一眼看清缺没缺：

```bash
cb doctor
```

三项都 ✓ 就绪，就能开始用了。

<details>
<summary>这三样分别去哪拿？（点开看）</summary>

- **AI Key**：aihubmix / DeepSeek / OpenAI / 通义 等任一家的 API Key。默认用 aihubmix。
- **Canvas Token**：登录 Canvas → 右上角 **账户 → 设置** → 最底部 **+ 新建访问令牌** → 复制。
- **登录 Cookie**：先浏览器登录 [oc.sjtu.edu.cn](https://oc.sjtu.edu.cn)；导入最省心的方式是装 [EditThisCookie](https://chromewebstore.google.com/detail/editthiscookie/fngmhnnpilhplaeedifhccceomclgfbg) 扩展，在该站点页面 Export 成文件，setup 时选「文件」填路径即可。Cookie 会过期，失效了重跑 `cb login` 刷新。

</details>

---

## 第三步：出笔记！

先给常用的课起个**别名**，这样以后不用记那串数字 ID：

```bash
cb alias 数值 88918      # 把 88918 这门课叫做「数值」（88918 从 cb list 里看）
```

然后：

```bash
cb 数值                  # 整理这门课最新一讲：抓转录 + AI 生成笔记
```

等 1~3 分钟，终端会打印进度，最后告诉你笔记存哪了。看看它：

```bash
cb read 数值             # 看笔记（前半部分）
cb read 数值 --full      # 看完整笔记
```

🎉 到这就跑通了。日常就这两条：`cb 数值` 出笔记、`cb read 数值` 看笔记。

---

## 更多常用招式

```bash
# 换一讲（0 是最新，1 是次新…）
cb all 数值 -i 3

# 看这门课有哪些回放（最近两周）
cb videos 数值 -s 2w

# 只要转录原文、不生成笔记（免费、快）
cb fetch 数值 -l         # 下载转录
cb read 数值 --txt       # 看转录纯文本

# 不花钱：只用平台自带摘要（质量一般，但免费）
cb notes 数值 -l --free

# 换个 AI 模型
cb notes 数值 -l -m deepseek/deepseek-chat

# 重新生成、覆盖旧笔记
cb notes 数值 -l --force

# 用大白话让它猜你要干嘛
cb ask "帮我整理 数值 最新一讲"
```

> 忘了命令？随时敲 `cb` 看手册，或 `cb 某命令 -h` 看那条命令的详细参数（如 `cb all -h`）。

---

## 笔记存在哪

```text
data/downloads/<课程ID>/
  transcripts/            转录（.json 原始 + .txt 纯文本）
  platform_summaries/     平台自带的摘要
  notes/                  ← 你要的笔记就在这，.md 文件
```

---

## 要花多少钱 / 多久

以一节 **55 分钟**的课、默认模型 `qwen3-max` 为例：

| | |
|---|---|
| 耗时 | **约 1.5 ~ 3 分钟** |
| 费用 | **约 ¥0.5 起** |

省钱选项：

| 模型 | 单节课 | 说明 |
|------|-------|------|
| `qwen-plus` | ~¥0.015 | 最便宜，质量略低 |
| `qwen-turbo` | ~¥0.05 | 快、便宜 |
| `qwen3-max`（默认） | ~¥0.5+ | 质量最稳，适合正经复习笔记 |
| `--free` | ¥0 | 不用 AI，只出平台原始摘要 |

---

## 支持的 AI 提供商

用 `-m 提供商/模型` 切换，对应的 key 写进 `.env`：

| 提供商 | 示例 | .env 里写 |
|--------|------|----------|
| aihubmix（默认） | `qwen3-max` | `LLM_API_KEY=...` |
| DeepSeek | `deepseek/deepseek-chat` | `DEEPSEEK_API_KEY=...` |
| OpenAI | `openai/gpt-4o` | `OPENAI_API_KEY=...` |
| 阿里云通义 | `qwen/qwen-max` | `DASHSCOPE_API_KEY=...` |
| 硅基流动 | `siliconflow/Qwen3-235B-A22B` | `SILICONFLOW_API_KEY=...` |

任何兼容 OpenAI 接口的服务，也可以直接在 `config.yaml` 里写 `base_url` / `model`。

---

## 出问题了？对号入座

**任何命令说"没配置 XX / 找不到 token"**
→ 跑 `cb doctor` 看缺哪样，再 `cb setup` 补上。不用手动建文件。

**抓取/`fetch` 说登录态、Cookie 失效**
→ 重跑 `cb login`，按提示导入一次（`cb login ~/cookies.json` 最稳）。Cookie 过期是正常现象。

**笔记生成失败 / 内容为空**
→ 先确认 `.env` 里 `LLM_API_KEY` 填对了。想快速排除是不是网络/转录的问题，先跑 `cb notes 数值 -l --free`（不走 AI）看看能不能出东西。

**连不上、超时**
→ 检查网络 / VPN / 代理。校内网或挂梯子时最稳。

**想覆盖已经生成的笔记**
→ 加 `--force`，如 `cb notes 数值 -l --force`。

---

## 给进阶用户

- 命令别名很多：`cb ls` = `cb list`，`cb a` = `cb all`，`cb n` = `cb notes`，`cb r` = `cb read`。
- 老写法仍兼容：`cb all --course 88918 --latest`。
- 没装脚本也能跑：`python -m course_buddy_v2.cli list`。
- `data/` 目录很大，已被 `.gitignore` 忽略；`.env` 和 `config.yaml`（含密钥）也不进 git。
