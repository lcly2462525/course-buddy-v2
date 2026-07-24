// 笔记生成：notes.py 的浏览器移植版。
// 转录 -> 清洗 -> 按时间/字数分片 -> 各片并发整理 -> 一次性合并 -> 修 LaTeX。

import { fmtTime } from "./util.js";
import { callLLM } from "./llm.js";

export function transcriptToLines(payload) {
  const items = payload?.data?.afterAssemblyList || [];
  const lines = [];
  for (const item of items) {
    const text = (item.res || "").trim();
    if (!text) continue;
    lines.push({ start: Math.floor((item.bg || 0) / 1000), text });
  }
  return lines;
}

const FILLER_RE = /^(嗯+|啊+|呃+|哦+|嗯啊|那个|就是说|OK+|okay|对吧|是吧|好的|好吧)$/i;
const SPAM_RE = /请不吝点赞\s*订阅\s*转发\s*打赏.*?栏目/g;

function cleanTranscript(text) {
  const lines = text.split("\n");
  const cleaned = [];
  let prev = "";
  for (const line of lines) {
    const stripped = line.trim();
    if (FILLER_RE.test(stripped)) continue;
    if (stripped === prev && stripped) continue; // 连续重复只留一次
    prev = stripped;
    cleaned.push(line);
  }
  let out = cleaned.join("\n");
  out = out.replace(SPAM_RE, "");
  return out.trim();
}

function buildTranscriptText(lines, chunkSeconds = 300) {
  if (!lines.length) return "";
  const out = [];
  let currentChunk = -1;
  for (const line of lines) {
    const chunk = Math.floor(line.start / chunkSeconds);
    if (chunk !== currentChunk) {
      currentChunk = chunk;
      out.push(`\n[${fmtTime(chunk * chunkSeconds)}]`);
    }
    out.push(line.text);
  }
  return cleanTranscript(out.join("\n").trim());
}

function buildChunks(lines, { chunkMinutes, maxChars }) {
  if (!lines.length) return [];
  const chunks = [];
  const chunkSeconds = Math.max(1, chunkMinutes) * 60;
  let current = [];
  let charLen = 0;
  let chunkStart = lines[0].start;

  const flush = () => {
    if (!current.length) return;
    chunks.push({
      start: current[0].start,
      end: current[current.length - 1].start,
      text: buildTranscriptText(current),
    });
    current = [];
    charLen = 0;
  };

  for (const line of lines) {
    const lineLen = line.text.length + 16;
    const hitTime = current.length && line.start - chunkStart >= chunkSeconds;
    const hitChar = current.length && charLen + lineLen > maxChars;
    if (hitTime || hitChar) {
      flush();
      chunkStart = line.start;
    }
    if (!current.length) chunkStart = line.start;
    current.push(line);
    charLen += lineLen;
  }
  flush();
  return chunks;
}

// —— LaTeX 小修 —— //
function fixInlineMath(text) {
  return text
    .replace(/(?<!\$)\$ ([^$\n]+?) \$(?!\$)/g, "$$$1$$")
    .replace(/^(\s*\$\$)\s+$/gm, "$1")
    .replace(/\\\[([\s\S]+?)\\\]/g, "$$$$$1$$$$");
}

// —— 平台摘要参考 & 兜底 —— //
function buildPlatformRef(summary) {
  const d = summary?.data || {};
  const parts = [];
  if (d.fullOverview) parts.push(`概要：${d.fullOverview}`);
  if ((d.keyPoints || []).length) parts.push("关键点：" + d.keyPoints.join("；"));
  if ((d.documentSkims || []).length) {
    parts.push("分段：" + d.documentSkims.map((s) => `[${s.time || "?"}] ${s.overview || ""}`).join(" | "));
  }
  return parts.join("\n");
}

function fallbackNotes(courseName, dateStr, title, lines, summary) {
  const d = summary?.data || {};
  const md = [
    `# ${courseName} · ${dateStr} · ${title}`,
    "",
    "> 未配置可用 LLM，以下为平台摘要和转录摘录。",
    "",
    "## 一、平台概要",
    "",
    d.fullOverview || "暂无平台概要。",
    "",
    "### 关键点",
  ];
  md.push(...((d.keyPoints || []).length ? d.keyPoints.map((k) => `- ${k}`) : ["- 暂无"]));
  md.push("", "## 二、分段概要");
  md.push(...((d.documentSkims || []).length
    ? d.documentSkims.map((s) => `- [${s.time || "?"}] ${s.overview || ""}`)
    : ["- 暂无"]));
  md.push("", "## 三、转录摘录");
  for (const line of lines.slice(0, 40)) md.push(`- [${fmtTime(line.start)}] ${line.text}`);
  md.push("");
  return md.join("\n");
}

// —— Prompts（与 notes.py 保持一致） —— //
function buildPrompt(courseName, dateStr, title, transcriptText, summary) {
  const ref = buildPlatformRef(summary);
  const refBlock = ref ? `\n参考（仅辅助纠错，与转录冲突时以转录为准）：\n${ref}\n` : "";
  return `将以下课堂语音转录整理成结构化笔记。来源：「${courseName}」${dateStr}，${title}。
${refBlock}
规则：
- 纠正术语/公式/人名的转录错误，不确定标 \`[?]\`
- 口述数学用 LaTeX：行内 \`$...$\`，块级 \`$$...$$\`（禁用 \`\\[...\\]\`），范数 \`\\lVert x \\rVert\`
- 老师强调"重要/会考/注意"处用 \`> ⚠️ **重点**：\`
- 不添加转录外内容，不臆测，不重复，同一内容只写一次
- 总体概要只做简明压缩，不要展开成长段说明
- 尽量按老师上课的原始展开顺序组织内容，不要把后面才讲的内容提前
- 遇到定义、定理、证明、推导、计算、例题时，尽量少跳步，保留关键中间式和理由

输出格式：

# ${courseName} · ${dateStr} · ${title}

## 一、总体概要
- 用尽量少的条目概括本讲主线
### 重要知识点

## 二、详细内容
按讲课逻辑分小标题展开，含推导、公式、例子。
若存在推导/证明/计算过程，优先写成 \`1. 2. 3.\` 的步骤式展开，保留中间公式与结论。

## 三、课堂事务
签到/互动 | 课程安排通知 | 课后任务（\`- [ ]\` 格式）。无则注明"无"。

---

转录文本：

${transcriptText}
`;
}

function buildChunkPrompt(courseName, dateStr, title, i, total, start, end, text) {
  return `整理课堂转录分片 ${i}/${total}（${fmtTime(start)}-${fmtTime(end)}），课程「${courseName}」${dateStr}。

规则：只基于本段，纠正术语错误（不确定标\`[?]\`），保留公式/定理/结论/例子/强调点/课堂事务。公式用 \`$...$\` 或 \`$$...$$\`，禁用 \`\\[...\\]\`。
- 背景/直觉/讲故事/重复强调，简要概括即可
- 按本段实际讲述顺序整理
- 出现推导/证明/计算/例题，按 \`1. 2. 3.\` 列步骤，保留关键中间式
- 详细度向推导倾斜：叙述从简，推导从细

输出格式：
### 分片 ${i}（${fmtTime(start)}-${fmtTime(end)}）
**主题**：…
**内容**：…
**公式与结论**：…
**事务**：…（无则省略）

转录：

${text}
`;
}

function buildMergePrompt(parts, courseName, dateStr, title) {
  return `合并以下分段笔记为完整笔记。去重但不丢信息，不添加分段中没有的内容。

要求：
- 总体概要保持简洁，只保留主线和重要知识点
- 优先保持老师原本的讲解顺序和推导顺序
- 遇到推导/证明/计算，尽量保留中间步骤和中间公式
- 分段信息冲突时，以更具体、步骤更完整的一版为准

输出格式：
# ${courseName} · ${dateStr} · ${title}
## 一、总体概要（分条列主题 + 重要知识点）
## 二、详细内容（按逻辑分小标题，含推导/公式/例子；推导优先用 \`1. 2. 3.\`，公式用 \`$...$\`/\`$$...$$\`，禁用 \`\\[...\\]\`）
## 三、课堂事务（签到/通知/作业，无则注明）

老师强调处用 \`> ⚠️ **重点**：\`，不确定标 \`[?]\`。

分段笔记：

${parts.join("\n---\n")}
`;
}

// 简单并发池
async function mapPool(items, worker, limit = 4) {
  const results = new Array(items.length);
  let next = 0;
  async function run() {
    while (next < items.length) {
      const i = next++;
      results[i] = await worker(items[i], i);
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, run));
  return results;
}

export async function summarize({ transcriptPayload, summaryPayload, courseName, dateStr, title, llm, onProgress }) {
  const log = (m) => onProgress && onProgress(m);
  const lines = transcriptToLines(transcriptPayload);
  if (!lines.length) return `# ${courseName} · ${dateStr} · ${title}\n\n> 转录为空，无法生成笔记。\n`;

  if (!llm || !llm.enabled || !llm.apiKey) {
    log("未启用 LLM，输出平台摘要和转录摘录。");
    return fallbackNotes(courseName, dateStr, title, lines, summaryPayload);
  }

  const chunks = buildChunks(lines, {
    chunkMinutes: llm.notesChunkMinutes ?? 12,
    maxChars: llm.notesChunkMaxChars ?? 12000,
  });

  if (chunks.length <= 1) {
    log("单段模式，请求模型…");
    const text = buildTranscriptText(lines);
    const md = await callLLM(buildPrompt(courseName, dateStr, title, text, summaryPayload), llm, {
      maxTokens: 16000,
      onProgress,
      label: "整讲笔记",
    });
    return fixInlineMath(md);
  }

  log(`分 ${chunks.length} 段并发处理…`);
  const partials = await mapPool(
    chunks,
    async (chunk, idx) => {
      const i = idx + 1;
      try {
        const r = await callLLM(
          buildChunkPrompt(courseName, dateStr, title, i, chunks.length, chunk.start, chunk.end, chunk.text),
          llm,
          { maxTokens: llm.notesChunkOutputTokens ?? 3200, label: `分片 ${i}/${chunks.length}`, onProgress }
        );
        return r;
      } catch (e) {
        log(`分片 ${i}/${chunks.length} 失败：${e.message}`);
        return null;
      }
    },
    4
  );

  const good = partials.filter(Boolean);
  if (!good.length) {
    log("所有分片失败，回退到平台摘要。");
    return fallbackNotes(courseName, dateStr, title, lines, summaryPayload);
  }
  if (good.length === 1) return fixInlineMath(good[0]);

  log(`合并 ${good.length} 个分片…`);
  try {
    const merged = await callLLM(buildMergePrompt(good, courseName, dateStr, title), llm, {
      maxTokens: llm.notesMergeMaxTokens ?? 12000,
      label: "总合并",
      onProgress,
    });
    return fixInlineMath(merged);
  } catch (e) {
    log(`合并失败（${e.message}），直接拼接分片。`);
    return fixInlineMath(good.join("\n\n---\n\n"));
  }
}
