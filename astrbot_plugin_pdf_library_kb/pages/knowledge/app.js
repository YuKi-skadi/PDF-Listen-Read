const bridge = window.AstrBotPluginPage;
const $ = (id) => document.getElementById(id);

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  }[char]));
}

function formatTime(value) {
  if (!value) return "尚未同步";
  const date = new Date(Number(value) * 1000);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

async function loadStats() {
  const data = await bridge.apiGet("stats");
  $("stats").innerHTML = [
    ["知识库", data.kb_name || "未创建"],
    ["已索引论文", data.mapped_papers ?? 0],
    ["知识库文档", data.document_count ?? 0],
    ["最后同步", formatTime(data.last_sync_at)],
    ["朗读器地址", data.reader_base_url || "未设置"],
  ].map(([key, value]) => `<div><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("");
  $("status").textContent = data.last_error ? `最近错误：${data.last_error}` : "运行正常";
  $("status").className = data.last_error ? "status error" : "status ok";
}

async function loadProviders() {
  const data = await bridge.apiGet("providers");
  for (const key of ["embedding", "rerank"]) {
    $(key).innerHTML = (data[key] || []).length
      ? data[key].map((item) => `<li><code>${escapeHtml(item.id)}</code><span>${escapeHtml(item.model || item.type)}</span></li>`).join("")
      : "<li class='muted'>暂无已加载模型</li>";
  }
}

async function loadDocuments() {
  const data = await bridge.apiGet("documents");
  const documents = data.documents || [];
  $("documents").innerHTML = documents.length ? documents.map((item) => `
    <details class="document">
      <summary><span class="title">${escapeHtml(item.title)}</span><span class="meta">${escapeHtml(item.folder_name || "未分类")} · ${item.page_count || 0} 页 · ${item.segment_count || 0} 块</span></summary>
      <div class="document-body"><p>原文件：${escapeHtml(item.original_filename || "")}</p><p>同步时间：${escapeHtml(formatTime(item.synced_at))}</p><button class="secondary preview" data-paper-id="${escapeHtml(item.paper_id)}">查看索引片段</button><pre class="chunks"></pre></div>
    </details>`).join("") : "<p class='muted empty'>还没有同步论文，请先完成插件配置后点击立即同步。</p>";
  document.querySelectorAll(".preview").forEach((button) => button.addEventListener("click", async () => {
    const body = button.closest(".document-body");
    const output = body.querySelector(".chunks");
    output.textContent = "读取中…";
    try {
      const result = await bridge.apiGet(`documents/${encodeURIComponent(button.dataset.paperId)}`);
      output.textContent = (result.chunks || []).map((chunk) => `[${chunk.chunk_index}] ${chunk.content}`).join("\n\n");
    } catch (error) { output.textContent = `读取失败：${error.message}`; }
  }));
}

async function refresh() {
  try {
    $("status").textContent = "读取中…";
    await Promise.all([loadStats(), loadProviders(), loadDocuments()]);
  } catch (error) { $("status").textContent = `读取失败：${error.message}`; }
}

$("sync").addEventListener("click", async () => {
  $("sync").disabled = true;
  $("sync").textContent = "同步中…";
  try { await bridge.apiPost("sync", {}); await refresh(); }
  catch (error) { $("status").textContent = `同步失败：${error.message}`; }
  finally { $("sync").disabled = false; $("sync").textContent = "立即同步"; }
});
$("reload").addEventListener("click", refresh);
await bridge.ready();
await refresh();
