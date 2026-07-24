// 抓取链路：Canvas LTI 认证 -> 视频平台 token -> 回放列表 -> 转录 / 平台摘要。
// 这是 replay_api.py + transcript_api.py 的浏览器移植版。
// 关键红利：fetch 用 credentials:'include'，自动带上你在浏览器里已有的登录 cookie，
// 所以彻底绕开了原 CLI 版"cookie 过期 / 读不出来"的死结。

import { extractFormInputs, extractParam, safeName, inferCourseName } from "./util.js";

const OC = "https://oc.sjtu.edu.cn";
const VIDEO = "https://v.sjtu.edu.cn/jy-application-canvas-sjtu";
const EXTERNAL_TOOL_ID = "8329";

// LTI 认证的最后一跳是个 302，tokenId 藏在 Location 里。
// fetch 读不到 opaqueredirect 的响应头，所以用 webRequest 观察这一跳把 tokenId 抠出来。
function waitForTokenId(timeoutMs = 20000) {
  return new Promise((resolve, reject) => {
    const filter = { urls: ["*://v.sjtu.edu.cn/*lti3Auth/ivs*"] };
    let timer = null;
    function handler(details) {
      const tid = extractParam(details.redirectUrl || "", "tokenId");
      if (tid) {
        cleanup();
        resolve(tid);
      }
    }
    function cleanup() {
      try { chrome.webRequest.onBeforeRedirect.removeListener(handler); } catch (e) {}
      if (timer) clearTimeout(timer);
    }
    timer = setTimeout(() => {
      cleanup();
      reject(new Error("等待视频平台 tokenId 超时（LTI 认证可能失败）"));
    }, timeoutMs);
    chrome.webRequest.onBeforeRedirect.addListener(handler, filter);
  });
}

async function postForm(url, inputs) {
  const resp = await fetch(url, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams(inputs),
    redirect: "follow",
  });
  return resp;
}

async function postJsonWithToken(url, body, token) {
  const resp = await fetch(url, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", token },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status} @ ${url}`);
  return resp.json();
}

export async function getPlatformToken(courseId) {
  const toolResp = await fetch(`${OC}/courses/${courseId}/external_tools/${EXTERNAL_TOOL_ID}`, {
    credentials: "include",
  });
  if (!toolResp.ok) {
    throw new Error(`打开视频工具页失败（HTTP ${toolResp.status}）。是否已登录 Canvas？`);
  }
  const html1 = await toolResp.text();
  const f1 = extractFormInputs(html1, "oidc/login_initiations");
  if (!f1) throw new Error("未找到 LTI 登录表单（多半是 Canvas 登录态失效，请刷新并重新登录 oc.sjtu.edu.cn）。");

  const loginResp = await postForm(f1.action, f1.inputs);
  const html2 = await loginResp.text();
  const f2 = extractFormInputs(html2, "lti3/lti3Auth/ivs");
  if (!f2) throw new Error("未找到视频平台鉴权表单。");

  // 先挂好监听，再触发那一跳。
  const waiter = waitForTokenId();
  postForm(f2.action, f2.inputs).catch(() => {});
  const tokenId = await waiter;

  const tokenResp = await fetch(
    `${VIDEO}/lti3/getAccessTokenByTokenId?tokenId=${encodeURIComponent(tokenId)}`,
    { credentials: "include" }
  );
  if (!tokenResp.ok) throw new Error(`换取 access token 失败（HTTP ${tokenResp.status}）。`);
  const payload = (await tokenResp.json()).data || {};
  const accessToken = payload.token;
  if (!accessToken) throw new Error("接口未返回 access token。");
  const p = payload.params || {};
  const canvasCourseId = String(p.courId || p.canvasCourseId || p.courseId || courseId);
  return { accessToken, canvasCourseId };
}

function extractRecords(payload) {
  if (Array.isArray(payload)) return payload;
  const paths = [
    ["data", "records"],
    ["data", "list"],
    ["data", "rows"],
    ["data", "items"],
    ["data", "page", "records"],
    ["data"],
  ];
  for (const path of paths) {
    let cur = payload;
    for (const key of path) cur = cur && typeof cur === "object" ? cur[key] : undefined;
    if (Array.isArray(cur)) return cur;
  }
  return null;
}

export async function getVideoList(accessToken, canvasCourseId) {
  const enc = encodeURIComponent(canvasCourseId);
  const bodies = [
    { canvasCourseId: enc, pageIndex: 1, pageSize: 1000 },
    { canvasCourseId: enc },
    { canvasCourseId: canvasCourseId, pageIndex: 1, pageSize: 1000 },
    { courId: enc, pageIndex: 1, pageSize: 1000 },
    { courId: enc },
    { courId: canvasCourseId },
  ];
  for (const body of bodies) {
    const json = await postJsonWithToken(`${VIDEO}/directOnDemandPlay/findVodVideoList`, body, accessToken);
    const records = extractRecords(json);
    if (records) return records;
  }
  throw new Error("视频列表接口没有返回可识别的数据。");
}

export async function getVideoDetail(videoId, accessToken) {
  const resp = await fetch(`${VIDEO}/directOnDemandPlay/getVodVideoInfos`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/x-www-form-urlencoded", token: accessToken },
    body: new URLSearchParams({ playTypeHls: "true", id: videoId, isAudit: "true" }),
  });
  if (!resp.ok) throw new Error(`获取回放详情失败（HTTP ${resp.status}）。`);
  const payload = await resp.json();
  return payload.data && typeof payload.data === "object" ? payload.data : payload;
}

export function sortReplays(videos) {
  return [...videos].sort((a, b) => {
    const ka = (a.courseBeginTime || "") + (a.videoName || "");
    const kb = (b.courseBeginTime || "") + (b.videoName || "");
    return ka < kb ? -1 : ka > kb ? 1 : 0;
  });
}

// 打开一门课的会话：认证一次 + 拉列表一次。
// 返回的 videos 已按"最新在前"排好，index 0 = 最新一讲，供上层按索引批量取用。
export async function openCourseSession(courseId, onProgress) {
  const log = (m) => onProgress && onProgress(m);
  log("LTI 认证中…");
  const { accessToken, canvasCourseId } = await getPlatformToken(courseId);
  log("获取回放列表…");
  const raw = await getVideoList(accessToken, canvasCourseId);
  const videos = [...sortReplays(raw)].reverse(); // 最新在前
  return { accessToken, canvasCourseId, videos };
}

// 精简回放清单（供 UI 展示/选择）。
export function replaySummaries(session) {
  return session.videos.map((v, i) => ({
    index: i,
    videoName: v.videoName || "",
    courseBeginTime: v.courseBeginTime || "",
    courseEndTime: v.courseEndTime || "",
    courId: v.courId,
  }));
}

// 抓单讲的转录 + 平台摘要（会话已建立，不再重复认证）。
export async function bundleForReplay(accessToken, replay, onProgress) {
  const log = (m) => onProgress && onProgress(m);
  await getVideoDetail(replay.videoId, accessToken).catch(() => null);
  const target = replay.courId;

  log(`下载转录：${replay.videoName}…`);
  const transcriptPayload = await postJsonWithToken(
    `${VIDEO}/transfer/translate/detail`,
    { courseId: target, platform: 1 },
    accessToken
  );
  const summaryPayload = await postJsonWithToken(
    `${VIDEO}/course/summary/canvas/detail`,
    { courseId: target, platform: 1 },
    accessToken
  );

  const begin = (replay.courseBeginTime || "").slice(0, 10) || "unknown-date";
  const title = replay.videoName || `replay_${target}`;
  const filePrefix = safeName(`${begin}_${title}`);
  const courseName = inferCourseName(replay.videoName, "课程");
  return { filePrefix, transcriptPayload, summaryPayload, replay, courseName, dateStr: begin, title };
}

// 列出回放（单独入口，供只想看列表的场景）。
export async function listReplays(courseId, onProgress) {
  const session = await openCourseSession(courseId, onProgress);
  return replaySummaries(session);
}

// 兼容旧的单讲入口：selector = {latest:true} | {index:N(0=最新)} | {courId:C}
export async function fetchTranscriptBundle({ courseId, selector, onProgress }) {
  const session = await openCourseSession(courseId, onProgress);
  let replay;
  if (selector.courId != null) {
    replay = session.videos.find((v) => v.courId === selector.courId);
    if (!replay) throw new Error(`没有找到 courId=${selector.courId} 的回放。`);
  } else if (selector.index != null) {
    replay = session.videos[selector.index];
    if (!replay) throw new Error(`索引 ${selector.index} 超出回放范围。`);
  } else {
    replay = session.videos[0]; // 默认最新
  }
  if (!replay) throw new Error("没有可用回放。");
  return bundleForReplay(session.accessToken, replay, onProgress);
}
