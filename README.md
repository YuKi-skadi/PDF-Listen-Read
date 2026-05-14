# PDF-Listen-Read

⚠️ **免责声明**：本项目全部由 AI 构建，虽然经过测试但可能存在不可预见的 bug。使用前请确保了解风险，作者不对因使用本软件导致的任何损失负责。

[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

**将 PDF 论文转换为可听的语音，支持边读边看、文本编辑和本地语音合成** 

> 无需等待整篇转换完成，边生成边播放 | 点击文本跳转播放 | 本地 TTS 免费使用  
> 专为学术论文阅读优化，解决 PDF 识别错误 | 适合视力障碍者/通勤场景/多任务处理

## ✨ 核心功能

### 📚 智能 PDF 处理
- 自动提取文本并智能分段（修复换行/断词问题）
- 支持直接编辑识别内容（删除目录/页眉等干扰项）
- 中文论文优化分段（按句号/段落智能切割）

### 🔉 语音播放
- **边生成边播放**：无需等待整篇合成完成
- **点击跳转**：点击任意段落立即跳转播放
- **双模式 TTS**：
  - ✅ **本地 TTS**（默认）：使用 edge-tts（微软神经语音），**完全免费**，无需 API Key
  - ☁️ **云端 API**：支持阿里云百炼（CosyVoice）、OpenAI TTS 等
- 📥 **完整音频下载**：合并所有片段为一个 MP3 文件
- ⛔ **停止生成**：可随时终止耗时操作
- 🧠 **LLM 文本优化**：调用大语言模型将学术文本改写为更适合朗读的口语表达
- 🧹 **一键清除缓存**：清理所有生成的音频文件和文档数据

### 💻 用户体验
- 与播放进度同步的**高亮文本**（卡拉OK效果）
- 语速调节（0.5x ~ 2.0x）
- 语音角色切换（本地支持 4+ 种中文神经语音）
- 操作记录与状态反馈
- 播放控制和状态栏**固定在左下角**，滚动阅读时不消失

## 🚀 快速开始

### 1. 安装依赖
```bash
pip install -r requirements.txt
```

### 2. 启动服务
```bash
uvicorn app.server:app --host 0.0.0.0 --port 8000
```
访问 http://127.0.0.1:8000

### 3. 使用流程
1. **上传 PDF** 或 **直接编辑文本**
2. 点击"获取模型列表"（仅云端模式需要）
3. **选择语音模式**：
   - ✅ 本地 TTS：默认勾选，立即使用（推荐）
   - ☁️ 云端 API：填写 API Key 后选择模型
4. 点击"处理并加载"
5. （可选）点击"LLM 优化文本"按钮，用 AI 将原文改写为更口语化的版本
6. 点击播放按钮开始听书

## ⚙️ 配置说明

### 本地 TTS（默认）
无需配置，开箱即用，支持以下语音：
| 语音模型 | 适用场景 |
|----------|----------|
| `zh-CN-XiaoxiaoNeural` | 通用女声（推荐） |
| `zh-CN-YunxiNeural` | 年轻男声 |
| `zh-CN-YunjianNeural` | 沉稳男声 |
| `zh-CN-XiaoyiNeural` | 可爱女声 |

### 云端 API（可选）
1. 复制你的 API Key（如 [阿里云百炼](https://help.aliyun.com/zh/model-studio)）
2. 填写配置：
   - **API Base URL**：`https://dashscope.aliyuncs.com/compatible-mode/v1`
   - **TTS 模型**：`cosyvoice-v3-flash`（推荐）
   - **语音角色**：`longanyang`（v3 模型专属）
3. 点击"获取模型列表"自动填充选项

> 💡 提示：新用户通常有免费额度，[查看定价](https://help.aliyun.com/zh/model-studio/cosyvoice)

## 📦 部署选项

### 本地运行（推荐）
```bash
uvicorn app.server:app --host 127.0.0.1 --port 8000
```

### Docker 部署
```bash
# 构建镜像
docker build -t pdf-listen-read .

# 运行容器（推荐映射音频缓存目录以便管理）
docker run -d \
  -p 8000:8000 \
  -v /your/host/audio/path:/app/app/static/audio \
  --name pdf-listen-read \
  pdf-listen-read
```

> **群晖 NAS 部署**：在 Docker 套件中导入镜像后，创建容器时在「存储空间」添加映射：
> - 本地路径：`/docker/pdf-listen-read/audio`
> - 装载路径：`/app/app/static/audio`
> - 权限：读写
>
> 音频缓存文件将保存在该目录，可在 File Station 中直接管理删除。

### Nginx 反向代理
```nginx
server {
    listen 80;
    server_name your-domain.com;
    
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
    }
}
```

## 📂 项目结构
```
PDF-Listen-Read/
├── app/
│   ├── __init__.py
│   ├── server.py            # FastAPI 后端
│   └── static/
│       ├── index.html       # Web 界面
│       ├── style.css        # 样式文件
│       ├── app.js           # 前端逻辑
│       └── audio/           # 音频缓存目录（运行时生成）
│           └── .gitkeep
├── Dockerfile               # Docker 构建文件
├── .dockerignore
├── .gitignore
├── .env.example             # 配置模板
├── requirements.txt         # 依赖列表
└── LICENSE                  # MIT 许可证
```

## 🤝 贡献指南
1. Fork 本仓库
2. 创建新分支 (`git checkout -b feature/your-feature`)
3. 提交更改 (`git commit -am 'Add some feature'`)
4. 推送分支 (`git push origin feature/your-feature`)
5. 发起 Pull Request

## 📜 许可证
本项目采用 [MIT 许可证](LICENSE)，这意味着：
- ✅ 可以**随意修改代码**
- ✅ 可以**用于商业项目**
- ✅ 可以**私有化部署**
- ❌ 唯须保留原作者版权信息

---

> 由开发者社区驱动，为学术研究者打造的阅读工具  
> 🌐 [GitHub 项目地址](https://github.com/YuKi-skadi/PDF-Listen-Read)
