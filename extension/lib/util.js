// 通用小工具：HTML 解码、表单抽取、URL 参数、文件名、时间格式。
// 之所以用正则而不用 DOMParser：MV3 的 service worker 里没有 DOM。

export function decodeHtml(s) {
  if (!s) return "";
  return s
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&#x2F;/gi, "/")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">");
}

// 从 HTML 里找出 action 含 needle 的 <form>，抽出它的所有 <input name/value>。
export function extractFormInputs(html, actionNeedle) {
  const formRe = /<form\b[^>]*>[\s\S]*?<\/form>/gi;
  let m;
  while ((m = formRe.exec(html))) {
    const form = m[0];
    const action = (form.match(/action\s*=\s*["']([^"']+)["']/i) || [])[1] || "";
    if (!action.includes(actionNeedle)) continue;
    const inputs = {};
    const inputRe = /<input\b[^>]*>/gi;
    let im;
    while ((im = inputRe.exec(form))) {
      const tag = im[0];
      const name = (tag.match(/name\s*=\s*["']([^"']*)["']/i) || [])[1];
      if (!name) continue;
      const value = (tag.match(/value\s*=\s*["']([^"']*)["']/i) || [])[1] || "";
      inputs[name] = decodeHtml(value);
    }
    return { action: decodeHtml(action), inputs };
  }
  return null;
}

// 从 url 的 query 或 fragment 里取某个参数。
export function extractParam(url, key) {
  if (!url) return null;
  const re = new RegExp("[?&#]" + key + "=([^&#]+)");
  const m = url.match(re);
  return m ? decodeURIComponent(m[1]) : null;
}

export function safeName(value) {
  return (value || "").replace(/[\\/*?:"<>|]/g, "_").trim();
}

export function fmtTime(totalSeconds) {
  const s = Math.max(0, Math.floor(totalSeconds));
  const hours = Math.floor(s / 3600);
  const minutes = Math.floor((s % 3600) / 60);
  const seconds = s % 60;
  const p = (n) => String(n).padStart(2, "0");
  return `${p(hours)}:${p(minutes)}:${p(seconds)}`;
}

// 从回放标题里推断课程名："微分方程数值解(第42讲)" -> "微分方程数值解"
export function inferCourseName(videoName, fallback) {
  const name = videoName || "";
  const marker = name.indexOf("(第");
  if (marker > 0) return name.slice(0, marker);
  return name || fallback || "课程";
}
