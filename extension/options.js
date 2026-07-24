const $ = (id) => document.getElementById(id);

async function load() {
  const llm = (await chrome.storage.local.get("llm")).llm || {};
  $("baseUrl").value = llm.baseUrl || "https://aihubmix.com/v1";
  $("apiKey").value = llm.apiKey || "";
  $("model").value = llm.model || "qwen3-max";
  $("temperature").value = llm.temperature ?? 0.3;
}

$("preset").addEventListener("change", () => {
  const [url, model] = $("preset").value.split("|");
  if (url === "custom") return;
  $("baseUrl").value = url;
  if (model) $("model").value = model;
});

$("save").addEventListener("click", async () => {
  const prev = (await chrome.storage.local.get("llm")).llm || {};
  const llm = {
    ...prev,
    enabled: true,
    baseUrl: $("baseUrl").value.trim(),
    apiKey: $("apiKey").value.trim(),
    model: $("model").value.trim(),
    temperature: parseFloat($("temperature").value) || 0.3,
  };
  await chrome.storage.local.set({ llm });
  $("saved").textContent = "✓ 已保存";
  setTimeout(() => ($("saved").textContent = ""), 2000);
});

load();
