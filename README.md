# PDF-Listen-Read

一个面向论文阅读的 Web 应用：保存原始 PDF，管理多种正文版本，按文本版本生成和播放语音，并通过通用 HTTP 接口连接外部 Agent 或知识库工具。

> 本项目仍在持续开发中。使用前请备份论文库，并自行评估第三方模型 API 和本地 TTS 运行环境的风险。

## 主要功能

- 论文和文件夹管理，保留原始 PDF，并在阅读页切换“原文件 / 文本”。
- 四种彼此独立的文本版本：自动识别、手动导入、DeepSeek 读图识别、朗读优化。
- DeepSeek 读图识别只把论文纯正文放入阅读文本；标题、作者和摘要作为隐藏知识库元信息保存。
- 朗读优化始终针对当前选中的文本版本，重新优化时覆盖原来的朗读优化版本。
- 每个语音包绑定具体的文本版本；切换文本版本不会误播其他版本的语音。
- 支持分段播放、完整语音、任务进度、日志、暂停/停止和失败信息查看。
- 支持本地 Edge TTS、在线 TTS，以及通过 HTTP 中介调用外部本地大模型 TTS。
- 支持声音克隆音源保存和复用。
- 资源管理、备份、回收站和过期资源清理，适合 Docker/NAS 部署。
- 提供带 Token 的集成 API，可供 AstrBot 或其他 Agent 工具读取论文和知识库专用文本。

## 快速开始

### 本地运行

需要 Python 3.11 或更高版本，以及系统可用的 `ffmpeg`。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.server:app --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000>。

首次启动后，API Key、DeepSeek 读图参数和 TTS 参数可以在 WebUI 设置中填写。论文库默认保存在项目目录下的 `data/`；也可以通过环境变量指定位置：

```powershell
$env:PDF_LISTEN_DATA_DIR = 'D:\pdf-listen-read-data'
python -m uvicorn app.server:app --host 0.0.0.0 --port 8000
```

### Docker / NAS

```bash
docker build -t pdf-listen-read .
docker run -d \
  --name pdf-listen-read \
  -p 8000:8000 \
  -v /your/host/pdf-listen-read-data:/data \
  pdf-listen-read
```

容器内应用使用 `/data` 保存 SQLite、原始 PDF、文本、音频、任务日志和备份。NAS 部署时建议只把可变数据映射到 `/data`，不要把运行时数据写回镜像层。更多部署说明见 [DOCKER_DEPLOY.md](DOCKER_DEPLOY.md)。

## 文本和知识库规则

阅读和朗读只使用用户当前选择的文本版本。知识库使用单独的选择规则：

1. 明确设置为知识库文本的手动导入版本；
2. DeepSeek 读图识别版本；
3. 两者都没有时跳过知识库同步。

自动识别文本和朗读优化文本不会被 AstrBot 插件作为知识库来源。标题、作者、摘要只作为隐藏元信息提供给知识库接口，不会混入阅读正文、朗读优化或 TTS 中介任务。

## 本地大模型 TTS 中介

中介源代码位于仓库的 `local-tts-bridge/`，它只负责接收语音任务、调用用户指定的外部 TTS 运行资源并导出 PDF 阅读器可导入的 ZIP。CUDA/ROCm 推理后端和模型不放入中介源代码或普通运行包，而是在中介设置页选择本机已有的资源目录。

中介协议说明见 [LOCAL_TTS_BRIDGE_PROTOCOL.md](LOCAL_TTS_BRIDGE_PROTOCOL.md)。中介封装版不提交到 Git，因为其中包含体积较大的普通运行依赖；稳定版本应作为 GitHub Release 附件提供。

## 外部 Agent / AstrBot 对接

先在 PDF 阅读器配置 `PDF_LISTEN_INTEGRATION_TOKEN`，外部请求使用：

```http
Authorization: Bearer <同一个 Token>
```

常用接口：

```text
GET  /api/integration/papers
GET  /api/integration/papers/{paper_id}
GET  /api/integration/papers/{paper_id}/text
GET  /api/integration/papers/{paper_id}/knowledge
GET  /api/integration/papers/{paper_id}/sync-state
POST /api/integration/papers/{paper_id}/sync-ack
POST /api/integration/papers/{paper_id}/delete-ack
```

其中 `/knowledge` 只返回符合知识库规则的正文分段及标题、作者、摘要；如果没有手动或读图文本会返回 404。完整请求示例见 [INTEGRATION_API.md](INTEGRATION_API.md)。

配套 AstrBot 插件位于 `astrbot_plugin_pdf_library_kb/`，复制到 AstrBot 插件目录后配置朗读器地址、集成 Token 和 Embedding Provider 即可。

## 仓库结构

```text
PDF-Listen-Read/
├── app/                              # FastAPI 后端和 WebUI
├── local-tts-bridge/                 # 本地 TTS 中介源代码
├── astrbot_plugin_pdf_library_kb/    # AstrBot 知识库插件
├── Dockerfile
├── requirements.txt
├── LOCAL_TTS_BRIDGE_PROTOCOL.md
├── INTEGRATION_API.md
└── LICENSE
```

运行数据、模型、CUDA/ROCm 后端、Python 虚拟环境和 PyInstaller 构建缓存均不应提交到仓库。

## 许可证

本项目采用 [MIT License](LICENSE)。第三方模型、语音服务和运行时资源遵循其各自许可证。
