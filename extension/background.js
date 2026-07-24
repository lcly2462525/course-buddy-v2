// Service worker：编排抓取 + 生成 + 下载。
// UI（popup / 页面面板）通过端口驱动这里，进度通过端口回传。
// 批量：只认证一次（openCourseSession），再按 indices 循环取每一讲。

import { openCourseSession, replaySummaries, bundleForReplay } from "./lib/canvas.js";
import { transcriptToLines, summarize } from "./lib/notes.js";
import { fmtTime } from "./lib/util.js";

const DEFAULT_LLM = {
  enabled: true,
  baseUrl: "https://aihubmix.com/v1",
  apiKey: "",
  model: "qwen3-max",
  temperature: 0.3,
  notesChunkMinutes: 12,
  notesChunkMaxChars: 12000,
  notesChunkOutputTokens: 3200,
  notesMergeMaxTokens: 12000,
};

async function getLlmConfig() {
  const stored = (await chrome.storage.local.get("llm")).llm || {};
  return { ...DEFAULT_LLM, ...stored };
}

// service worker 里 createObjectURL 不稳，用 data URL 触发下载。
function downloadText(filename, text, mime = "text/markdown") {
  const url = `data:${mime};charset=utf-8,` + encodeURIComponent(text);
  return chrome.downloads.download({ url, filename, saveAs: false });
}

function transcriptToPlainText(payload) {
  return transcriptToLines(payload)
    .map((l) => `[${fmtTime(l.start)}] ${l.text}`)
    .join("\n");
}

// 把 indices 规整：去重、排序、过滤越界。
function normalizeIndices(indices, max) {
  const set = new Set();
  for (const i of indices || []) {
    const n = Number(i);
    if (Number.isInteger(n) && n >= 0 && n < max) set.add(n);
  }
  return [...set].sort((a, b) => a - b);
}

async function runBatch({ courseId, indices, mode, port }) {
  const send = (type, payload = {}) => {
    try { port.postMessage({ type, ...payload }); } catch (e) {}
  };
  const onProgress = (m) => send("progress", { message: m });

  const session = await openCourseSession(courseId, onProgress);
  const list = normalizeIndices(indices, session.videos.length);
  if (!list.length) {
    send("error", { message: "没有选中任何回放。" });
    return;
  }
  const llm = mode === "notes" ? await getLlmConfig() : null;
  if (mode === "notes" && !llm.apiKey) {
    chrome.runtime.openOptionsPage();
    send("error", { message: "还没配 LLM API Key，已打开设置页。填好后再来。" });
    return;
  }

  const total = list.length;
  let ok = 0;
  const files = [];
  for (let k = 0; k < total; k++) {
    const idx = list[k];
    const replay = session.videos[idx];
    send("progress", { message: `【${k + 1}/${total}】${replay.videoName}` });
    try {
      const bundle = await bundleForReplay(session.accessToken, replay, onProgress);
      if (mode === "transcript") {
        const txt = transcriptToPlainText(bundle.transcriptPayload);
        await downloadText(`${bundle.filePrefix}.txt`, txt, "text/plain");
        files.push(`${bundle.filePrefix}.txt`);
      } else {
        send("progress", { message: `生成笔记：${bundle.title}…` });
        const md = await summarize({
          transcriptPayload: bundle.transcriptPayload,
          summaryPayload: bundle.summaryPayload,
          courseName: bundle.courseName,
          dateStr: bundle.dateStr,
          title: bundle.title,
          llm,
          onProgress,
        });
        await downloadText(`${bundle.filePrefix}.md`, md, "text/markdown");
        files.push(`${bundle.filePrefix}.md`);
      }
      ok++;
      send("item", { message: `✅ ${files[files.length - 1]}`, done: k + 1, total });
    } catch (e) {
      send("item", { message: `❌ 第${idx}讲失败：${e && e.message ? e.message : e}`, done: k + 1, total });
    }
  }
  send("done", { message: `完成 ${ok}/${total} 讲。`, files });
}

// —— 端口式长任务（带进度） —— //
chrome.runtime.onConnect.addListener((port) => {
  port.onMessage.addListener(async (msg) => {
    const send = (type, payload = {}) => {
      try { port.postMessage({ type, ...payload }); } catch (e) {}
    };
    try {
      if (msg.type === "listReplays") {
        send("progress", { message: "认证并拉取回放列表…" });
        const session = await openCourseSession(msg.courseId, (m) => send("progress", { message: m }));
        send("replays", { replays: replaySummaries(session) });
      } else if (msg.type === "batchTranscript") {
        await runBatch({ courseId: msg.courseId, indices: msg.indices, mode: "transcript", port });
      } else if (msg.type === "batchNotes") {
        await runBatch({ courseId: msg.courseId, indices: msg.indices, mode: "notes", port });
      } else {
        send("error", { message: "未知指令：" + msg.type });
      }
    } catch (e) {
      send("error", { message: e && e.message ? e.message : String(e) });
    }
  });
});
