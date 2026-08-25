# astrbot_plugin_pdf_library_kb

将 PDF-Listen-Read 中符合规则的论文内容同步到 AstrBot 原生知识库。插件只在 AstrBot 内保存索引映射，论文正文、原 PDF、文件夹结构和语音包仍以朗读器为准。

## 安装

把本目录复制到 AstrBot 的插件目录：

```text
AstrBot/data/plugins/astrbot_plugin_pdf_library_kb/
```

然后在 AstrBot WebUI 中启用插件并重载。

## 配置

插件设置中填写：

- `reader_base_url`：朗读器地址。两套服务在同一个 Docker 网络时，使用容器名，例如 `http://pdf-listen-read:8000`。
- `reader_integration_token`：朗读器的 `PDF_LISTEN_INTEGRATION_TOKEN`，用于访问受保护的机器接口。
- `embedding_provider_id`：AstrBot 已加载的 Embedding Provider ID；留空会使用当前第一个 Embedding Provider。
- `rerank_provider_id`：可选的 Rerank Provider ID。
- 自动同步开关与同步间隔。

AstrBot 插件详情页的“论文知识库”页面会显示可用 Provider ID、当前文档数量、同步错误和索引片段。这个页面是只读预览；需要修改论文时回到 PDF-Listen-Read，避免两边文本不一致。

## 知识库文本选择

插件通过朗读器的 `GET /api/integration/papers/{paper_id}/knowledge` 获取知识库专用文本，不直接读取当前阅读文本。请求需要携带与朗读器一致的 Bearer Token。选择顺序是：

1. 明确标记为知识库文本的手动导入版本；
2. DeepSeek 读图识别版本；

如果两者都不存在，插件跳过该论文，不会索引自动识别文本或朗读优化文本。

知识库文档会在正文前附加论文标题、作者和摘要。这些内容是隐藏元信息，不会写入阅读正文，也不会发送给朗读优化或 TTS 中介。

手动导入正文时，PDF 阅读器会询问是否填写知识库信息。如果后来才补填，切换到该手动文本版本时会出现红色提示，点击即可补录。手动文本一旦明确设为知识库文本，就会优先于读图版本。

## 命令与 LLM 工具

- `/paper_kb_sync`：立即同步。
- `/paper_kb_preview`：查看已同步论文列表。
- `search_paper_knowledge`：注册给 AstrBot Agent 的 LLM Tool，用于根据问题检索论文片段。

插件每次同步都会：

1. 新论文上传到 AstrBot 原生 KB；
2. 朗读器的活动文本版本变化时，删除旧文档并重新向量化；
3. 朗读器中删除的论文从 AstrBot KB 中删除对应文档。

单篇论文会按最多 96 个文本块拆分成多个 AstrBot 索引文档，但插件预览和检索仍按原论文归属处理。这样可以降低长论文在低性能 NAS 上一次性写入 FAISS/SQLite 的压力。

Embedding 上传批次固定为 25，兼容当前 DashScope/Qwen Embedding 接口对 `input.contents` 的批量限制。

## 当前版本说明

这是一个采用轮询同步的配套插件。任何 Agent 都可以按照朗读器的 [集成 API](../INTEGRATION_API.md) 读取论文列表、当前文本或知识库专用文本；不需要依赖本插件的内部实现。
