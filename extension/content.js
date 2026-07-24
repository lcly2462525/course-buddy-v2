// Canvas 课程页右下角面板：载入回放清单 → 勾选多讲（含快捷选择）→ 批量下载/生成。
(function () {
  const m = location.pathname.match(/\/courses\/(\d+)/);
  if (!m) return;
  const courseId = m[1];
  if (document.getElementById("cb-panel-root")) return;

  let replays = []; // {index, videoName, courseBeginTime, courId}
  const selected = new Set();

  // —— 样式 —— //
  const style = document.createElement("style");
  style.textContent = `
    #cb-panel-root{position:fixed;right:16px;bottom:16px;z-index:2147483647;font-family:system-ui,-apple-system,sans-serif;font-size:13px;}
    #cb-launch{cursor:pointer;border:none;border-radius:24px;padding:10px 16px;color:#fff;background:#2563eb;box-shadow:0 3px 10px rgba(0,0,0,.3);}
    #cb-panel{display:none;width:320px;background:#fff;border-radius:14px;box-shadow:0 8px 30px rgba(0,0,0,.28);overflow:hidden;}
    #cb-panel.open{display:block;}
    .cb-head{display:flex;justify-content:space-between;align-items:center;padding:10px 12px;background:#2563eb;color:#fff;}
    .cb-head b{font-size:13px;}
    .cb-x{cursor:pointer;background:transparent;border:none;color:#fff;font-size:16px;}
    .cb-body{padding:10px 12px;}
    .cb-chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px;}
    .cb-chip{cursor:pointer;border:1px solid #d1d5db;background:#f9fafb;border-radius:14px;padding:3px 10px;font-size:12px;}
    .cb-chip:hover{background:#eef2ff;border-color:#2563eb;}
    .cb-list{max-height:220px;overflow:auto;border:1px solid #eee;border-radius:8px;padding:4px;}
    .cb-item{display:flex;gap:6px;align-items:flex-start;padding:4px 6px;border-radius:6px;}
    .cb-item:hover{background:#f3f4f6;}
    .cb-item label{cursor:pointer;line-height:1.3;}
    .cb-item .d{color:#6b7280;font-size:11px;}
    .cb-actions{display:flex;gap:6px;margin-top:8px;}
    .cb-actions button{flex:1;cursor:pointer;border:none;border-radius:9px;padding:9px;color:#fff;font-size:13px;}
    .cb-actions button:disabled{opacity:.5;cursor:default;}
    .cb-primary{background:#2563eb;} .cb-green{background:#059669;} .cb-ghost{background:#6b7280;}
    #cb-log{margin-top:8px;font-size:12px;color:#333;background:#f3f4f6;border-radius:8px;padding:8px;max-height:120px;overflow:auto;white-space:pre-wrap;}
    #cb-count{color:#2563eb;font-weight:600;}
  `;
  document.documentElement.appendChild(style);

  // —— 结构 —— //
  const root = document.createElement("div");
  root.id = "cb-panel-root";
  root.innerHTML = `
    <div id="cb-panel">
      <div class="cb-head"><b>Course Buddy · 转录与笔记</b><button class="cb-x" id="cb-close">×</button></div>
      <div class="cb-body">
        <div class="cb-actions" style="margin:0 0 8px">
          <button class="cb-ghost" id="cb-load">载入回放清单</button>
        </div>
        <div class="cb-chips" id="cb-chips" style="display:none">
          <span class="cb-chip" data-q="latest">最新一讲</span>
          <span class="cb-chip" data-q="last3">最近3讲</span>
          <span class="cb-chip" data-q="last5">最近5讲</span>
          <span class="cb-chip" data-q="2w">最近两周</span>
          <span class="cb-chip" data-q="1m">最近一月</span>
          <span class="cb-chip" data-q="all">全部</span>
          <span class="cb-chip" data-q="none">清空</span>
        </div>
        <div class="cb-list" id="cb-list" style="display:none"></div>
        <div class="cb-actions" id="cb-run" style="display:none">
          <button class="cb-primary" id="cb-dl">⬇ 下载转录 (<span id="cb-count">0</span>)</button>
          <button class="cb-green" id="cb-note">📝 生成笔记</button>
        </div>
        <div id="cb-log" style="display:none">就绪。</div>
      </div>
    </div>
    <div style="text-align:right;margin-top:8px"><button id="cb-launch">📝 Course Buddy</button></div>
  `;
  document.body.appendChild(root);

  const $ = (id) => root.querySelector(id);
  const panel = $("#cb-panel");
  const logEl = $("#cb-log");
  const listEl = $("#cb-list");
  const countEl = $("#cb-count");

  function log(msg, replace = false) {
    logEl.style.display = "block";
    logEl.textContent = replace ? msg : logEl.textContent + "\n" + msg;
    logEl.scrollTop = logEl.scrollHeight;
  }
  function updateCount() {
    countEl.textContent = String(selected.size);
    root.querySelectorAll(".cb-item input").forEach((cb) => {
      cb.checked = selected.has(Number(cb.dataset.idx));
    });
  }
  function thresholdDate(days) {
    const d = new Date(Date.now() - days * 86400000);
    return d.toISOString().slice(0, 10);
  }
  function applyQuick(q) {
    selected.clear();
    if (q === "none") {
      /* 清空 */
    } else if (q === "all") {
      replays.forEach((r) => selected.add(r.index));
    } else if (q === "latest") {
      if (replays[0]) selected.add(0);
    } else if (q === "last3" || q === "last5") {
      const n = q === "last3" ? 3 : 5;
      replays.slice(0, n).forEach((r) => selected.add(r.index));
    } else if (q === "2w" || q === "1m") {
      const th = thresholdDate(q === "2w" ? 14 : 31);
      replays.forEach((r) => {
        if ((r.courseBeginTime || "").slice(0, 10) >= th) selected.add(r.index);
      });
    }
    updateCount();
  }

  function renderList() {
    listEl.innerHTML = "";
    replays.forEach((r) => {
      const row = document.createElement("div");
      row.className = "cb-item";
      const star = r.index === 0 ? "★ " : "";
      row.innerHTML = `
        <input type="checkbox" data-idx="${r.index}" id="cb-cb-${r.index}" />
        <label for="cb-cb-${r.index}"><div>${star}${r.videoName || "(未命名)"}</div>
        <div class="d">${(r.courseBeginTime || "").slice(0, 16)}</div></label>`;
      listEl.appendChild(row);
    });
    listEl.querySelectorAll("input").forEach((cb) => {
      cb.addEventListener("change", () => {
        const i = Number(cb.dataset.idx);
        if (cb.checked) selected.add(i);
        else selected.delete(i);
        countEl.textContent = String(selected.size);
      });
    });
    $("#cb-chips").style.display = "flex";
    listEl.style.display = "block";
    $("#cb-run").style.display = "flex";
  }

  // —— 端口任务 —— //
  function setBusy(b) {
    ["#cb-load", "#cb-dl", "#cb-note"].forEach((s) => ($(s).disabled = b));
  }
  function connect(onMsg) {
    const port = chrome.runtime.connect();
    port.onMessage.addListener(onMsg);
    return port;
  }

  $("#cb-load").addEventListener("click", () => {
    setBusy(true);
    log("载入回放清单…", true);
    const port = connect((msg) => {
      if (msg.type === "progress") log(msg.message);
      else if (msg.type === "replays") {
        replays = msg.replays;
        renderList();
        log(`共 ${replays.length} 讲。用上面的快捷按钮或手动勾选。`);
        setBusy(false);
        port.disconnect();
      } else if (msg.type === "error") {
        log("❌ " + msg.message);
        setBusy(false);
        port.disconnect();
      }
    });
    port.postMessage({ type: "listReplays", courseId });
  });

  function runBatch(kind) {
    if (!selected.size) return log("请先选择要处理的回放（可用快捷按钮）。");
    const indices = [...selected].sort((a, b) => a - b);
    setBusy(true);
    log(`开始处理 ${indices.length} 讲…`, true);
    const port = connect((msg) => {
      if (msg.type === "progress" || msg.type === "item") log(msg.message);
      else if (msg.type === "done") {
        log("✅ " + msg.message);
        setBusy(false);
        port.disconnect();
      } else if (msg.type === "error") {
        log("❌ " + msg.message);
        setBusy(false);
        port.disconnect();
      }
    });
    port.postMessage({ type: kind === "notes" ? "batchNotes" : "batchTranscript", courseId, indices });
  }

  root.querySelectorAll(".cb-chip").forEach((c) => c.addEventListener("click", () => applyQuick(c.dataset.q)));
  $("#cb-dl").addEventListener("click", () => runBatch("transcript"));
  $("#cb-note").addEventListener("click", () => runBatch("notes"));
  $("#cb-launch").addEventListener("click", () => panel.classList.toggle("open"));
  $("#cb-close").addEventListener("click", () => panel.classList.remove("open"));
})();
