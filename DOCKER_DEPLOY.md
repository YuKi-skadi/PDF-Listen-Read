# Docker / NAS 部署

容器内应用代码位于 `/app`，所有可变的论文库数据统一位于 `/data`。部署时只需要映射一个宿主机目录到 `/data`。

```bash
docker build -t pdf-listen-read:final .

docker save -o pdf-listen-read-final.tar pdf-listen-read:final

docker run -d \
  --name pdf-listen-read \
  --restart unless-stopped \
  -p 8000:8000 \
  -v /volume1/docker/pdf-listen-read:/data \
  pdf-listen-read:final
```

如果 PowerShell 不方便进入项目目录，也可以在项目目录中直接运行 `build-and-export.ps1`：

```powershell
Set-Location 'D:\path\to\PDF-Listen-Read'
.\build-and-export.ps1
```

访问 `http://NAS地址:8000`。

`/data` 中会保存 `library.db`、论文原 PDF、文本版本、语音包、音源和自动备份文件。首次部署前请确保宿主机目录具有读写权限。
