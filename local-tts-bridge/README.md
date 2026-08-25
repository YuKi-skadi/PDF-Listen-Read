# PDF Local TTS Bridge

PDF-Listen-Read 的本地大模型 TTS 中介服务。它通过 HTTP 接收 PDF 阅读器提交的文本版本和正文分段，调用用户在本机配置的外部 TTS 运行环境生成音频，最后导出可以被 PDF 阅读器导入的 ZIP 语音包。

中介自身只携带 UI、HTTP 服务、任务队列、资源扫描和普通 Python/Qt 运行依赖；CUDA/ROCm 推理后端和大模型保持在中介目录之外，由用户在设置页选择。这样可以避免复制大型模型，也方便在同一台电脑上切换不同推理环境。

## 运行方式

开发调试：

```powershell
python -m pip install -r requirements.txt
python main.py
```

正式使用建议运行封装版 `PdfLocalTTSBridge.exe`。启动后在设置页：

1. 设置监听端口，默认 `47840`；
2. 选择外部 TTS 资源根目录；
3. 点击扫描，软件会递归寻找可用的 Python、`tts_server.py`、模型目录和推理后端；
4. 选择运行时、后端和模型并保存；
5. 在 PDF 阅读器中填写中介 IP 和端口，测试连接后发送任务。

## 外部资源目录

扫描器支持常见的运行时目录名，也会递归查找包含 `tts_server.py` 和嵌入式 Python 的目录。一个推荐的通用布局如下：

```text
tts-resources/
├── runtime_cuda/
│   ├── python/python.exe
│   └── tts_server.py
├── runtime_rocm/
│   ├── python/python.exe
│   └── tts_server.py
├── runtime_cpu/
│   ├── python/python.exe
│   └── tts_server.py
└── models/
    └── <model-name>/config.json
```

模型可以放在共享的 `models/`，也可以放在某个运行时目录下。每个模型目录需要包含其推理服务要求的配置文件。软件会尝试探测 Torch 的 CUDA/ROCm 能力，并结合运行时脚本声明的后端显示可用选项。

如果资源提供方发布的是 ZIP 压缩包，可以使用：

```powershell
.\download_runtime_resources.ps1 `
  -RuntimeUrl 'https://example.invalid/runtime.zip' `
  -ModelsUrl 'https://example.invalid/models.zip' `
  -Destination 'D:\tts-resources'
```

这个脚本只负责下载和解压用户明确提供的资源地址，不内置任何特定模型、厂商或项目的下载地址。对于需要登录、授权或同意许可证的资源，请先按照资源提供方的要求完成授权。

## 任务和显存

PDF 阅读器发送任务后，中介先将任务放入待生成队列；用户点击开始后才启动外部 TTS 运行时。完成后可以在“已生成语音包”页面导出 ZIP。

任务完成后运行时默认保持启动，方便连续生成。点击“释放显存”时，中介会请求外部服务卸载模型并停止运行时进程，但不会关闭中介 UI 和监听服务。

配置、日志、任务记录和生成的语音包保存在中介目录的 `data/` 中。该目录是运行数据，不应提交到源代码仓库。

## 错误定位

中介 UI 的“运行日志”页面和任务失败弹窗会显示任务 ID、失败分段和异常详情。完整日志位于：

```text
data/bridge.log
data/tts-runtime.log
```

遇到“运行时未启动”“Python 不存在”“模型不支持音色”或连接被关闭时，优先查看任务详细信息和上述运行时日志。

## HTTP 接口

服务使用 `pdf-local-tts-1` 协议：

```text
GET  /health
POST /v1/jobs
GET  /v1/jobs/{job_id}
POST /v1/jobs/{job_id}/cancel
GET  /v1/jobs/{job_id}/package
```

PDF 阅读器提交的是明确的 `text_version_id` 和正文分段。中介不会重新提取 PDF，也不会读取论文库；它只处理请求中提交的文本。

语音包包含 `manifest.json`、`segments/*.wav` 和可选的 `full.wav`，其中保存论文 ID、文本版本 ID、分段 ID 和内容哈希。PDF 阅读器会校验这些信息后再导入，因此语音不会绑定到错误的文本版本。

完整字段和 ZIP 结构见主项目的 [LOCAL_TTS_BRIDGE_PROTOCOL.md](../LOCAL_TTS_BRIDGE_PROTOCOL.md)。

## Windows 打包

使用带 PyInstaller 的开发 Python：

```powershell
.\build_windows.ps1 -RuntimePython 'D:\path\to\python.exe'
```

默认输出到同级的 `pdf-local-tts-bridge-package\PdfLocalTTSBridge`。封装结果包含中介自身的普通运行依赖，但不包含 CUDA/ROCm 和模型。封装目录不建议提交到 Git，应作为 GitHub Release 附件或独立下载包发布。
