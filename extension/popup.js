const $ = (id) => document.getElementById(id);
const logEl = $("log");
function log(msg, replace = false) {
  logEl.textContent = replace ? msg : logEl.textContent + "\n" + msg;
  logEl.scrollTop = logEl.scrollHeight;
}

// 自动从当前 Canvas 课程页填课程 ID
chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
  const url = tabs[0]?.url || "";
  const m = url.match(/oc\.sjtu\.edu\.cn\/courses\/(\d+)/);
  if (m) $("courseId").value = m[1];
});

let replayCount = 0;

// popup 里下拉选一讲 -> 转成 indices 数组（页面面板支持多选，popup 走单讲即可）。
function currentIndices() {
  const v = $("replay").value;
  if (v === "latest") return [0];
  return [parseInt(v, 10)];
}

function runTask(type) {
  const courseId = $("courseId").value.trim();
  if (!courseId) return log("请先填课程 ID。", true);
  const buttons = [$("btnList"), $("btnTxt"), $("btnNote")];
  buttons.forEach((b) => (b.disabled = true));
  log("开始…", true);

  const port = chrome.runtime.connect();
  port.onMessage.addListener((msg) => {
    if (msg.type === "progress" || msg.type === "item") log(msg.message);
    else if (msg.type === "replays") {
      const sel = $("replay");
      sel.innerHTML = '<option value="latest">最新一讲</option>';
      msg.replays.forEach((r) => {
        const opt = document.createElement("option");
        opt.value = String(r.index);
        opt.textContent = `${r.index === 0 ? "★" : r.index} ${r.courseBeginTime?.slice(0, 10) || ""} ${r.videoName}`;
        sel.appendChild(opt);
      });
      replayCount = msg.replays.length;
      log(`共 ${replayCount} 讲。选一讲后点下载/生成；要多选或批量请用课程页右下角面板。`);
      buttons.forEach((b) => (b.disabled = false));
      port.disconnect();
    } else if (msg.type === "done") {
      log("✅ " + msg.message);
      buttons.forEach((b) => (b.disabled = false));
      port.disconnect();
    } else if (msg.type === "error") {
      log("❌ " + msg.message);
      buttons.forEach((b) => (b.disabled = false));
      port.disconnect();
    }
  });

  if (type === "listReplays") port.postMessage({ type, courseId });
  else port.postMessage({ type, courseId, indices: currentIndices() });
}

$("btnList").addEventListener("click", () => runTask("listReplays"));
$("btnTxt").addEventListener("click", () => runTask("batchTranscript"));
$("btnNote").addEventListener("click", () => runTask("batchNotes"));
$("openOptions").addEventListener("click", () => chrome.runtime.openOptionsPage());
