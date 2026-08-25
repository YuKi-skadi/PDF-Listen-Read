# PDF-Listen-Read 集成 API

这是面向 AstrBot、Agent 或其他自动化工具的机器对机器接口。接口只读论文库或回写同步状态，不直接暴露 WebUI 的本地管理操作。

## 认证

启动前设置环境变量：

```env
PDF_LISTEN_INTEGRATION_TOKEN=换成一段足够长的随机字符串
```

请求使用 Bearer Token：

```http
Authorization: Bearer <PDF_LISTEN_INTEGRATION_TOKEN>
```

未配置 Token 时，集成接口会返回 `503`；Token 错误时返回 `401`。

## 论文列表

```http
GET /api/integration/papers
GET /api/integration/papers?updated_since=2026-01-01T00:00:00+00:00
```

返回当前论文列表和已删除论文列表。外部工具可以保存上次的 `updated_at`，下一次用 `updated_since` 做增量同步。

## 论文与文本

```http
GET /api/integration/papers/{paper_id}
GET /api/integration/papers/{paper_id}/text
GET /api/integration/papers/{paper_id}/knowledge
```

`/text` 返回当前活动文本版本，适合需要读取用户当前阅读内容的工具。

`/knowledge` 返回知识库专用内容，选择规则固定为：

1. 明确设置为知识库文本的手动导入版本；
2. DeepSeek 读图识别版本；
3. 没有这两类文本时返回 `404`。

自动识别和朗读优化不会通过 `/knowledge` 返回。响应中的 `segments` 是正文分段，`knowledge` 字段包含隐藏的 `title`、`authors` 和 `abstract`。

示例：

```powershell
$headers = @{ Authorization = 'Bearer <TOKEN>' }
Invoke-RestMethod `
  -Uri 'http://127.0.0.1:8000/api/integration/papers/<PAPER_ID>/knowledge' `
  -Headers $headers
```

## 同步确认

知识库插件完成上传或删除后，可以回写：

```http
GET  /api/integration/papers/{paper_id}/sync-state
POST /api/integration/papers/{paper_id}/sync-ack
POST /api/integration/papers/{paper_id}/delete-ack
```

同步确认请求示例：

```json
{
  "text_version_id": "文本版本 ID",
  "remote_document_id": "远端文档 ID",
  "status": "synced",
  "error_message": null
}
```

`status` 支持 `pending`、`synced` 和 `error`。

## Agent 工具建议

一个通用 Agent 工具可以按以下流程工作：

1. 调用 `/papers` 列出论文；
2. 根据标题、文件名或文件夹筛选 `paper_id`；
3. 需要当前阅读内容时调用 `/text`，需要知识库内容时调用 `/knowledge`；
4. 把 `segments` 按段落或检索片段提供给模型，并保留 `paper_id`、标题和分段信息作为来源；
5. 如果 `updated_at` 变化，再重新读取并更新自己的索引。

不要把 PDF 阅读器的 SQLite 或文件目录直接挂载给 Agent；所有外部访问都通过 HTTP 接口完成。
