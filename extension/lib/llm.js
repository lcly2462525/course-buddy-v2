// 流式调用兼容 OpenAI 接口的 chat/completions。
// 必须 streaming：长生成超过边缘网关空闲超时（aihubmix ~60s）会被掐断。

export async function callLLM(prompt, llm, { maxTokens = 16000, onProgress, label = "模型请求" } = {}) {
  if (!llm.apiKey) throw new Error("未配置 LLM API Key（请在扩展设置页填写）。");
  const url = llm.baseUrl.replace(/\/+$/, "") + "/chat/completions";
  const body = {
    model: llm.model,
    messages: [{ role: "user", content: prompt }],
    max_tokens: maxTokens,
    temperature: llm.temperature ?? 0.3,
    stream: true,
  };
  if (onProgress) onProgress(`${label}：请求模型中…`);

  const resp = await fetch(url, {
    method: "POST",
    headers: { Authorization: "Bearer " + llm.apiKey, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok || !resp.body) {
    const preview = (await resp.text().catch(() => "")).slice(0, 200);
    throw new Error(`HTTP ${resp.status}: ${preview || "空响应"}`);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let out = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, idx).trim();
      buffer = buffer.slice(idx + 1);
      if (!line.startsWith("data:")) continue;
      const data = line.slice(5).trim();
      if (data === "[DONE]") {
        if (onProgress) onProgress(`${label}：完成。`);
        return out;
      }
      try {
        const piece = JSON.parse(data).choices?.[0]?.delta?.content;
        if (piece) out += piece;
      } catch (e) {
        /* 跳过无法解析的分片 */
      }
    }
  }
  if (!out) throw new Error("流式响应未返回内容。");
  if (onProgress) onProgress(`${label}：完成。`);
  return out;
}
