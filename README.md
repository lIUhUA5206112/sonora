# 拾音 Sonora（开源版）

> 本地音频转换与标签工具：把 FLAC / MP3 / M4A / OGG / WAV 批量转成 MP3 或 FLAC，
> 保留标题、歌手、专辑、音轨号、封面和歌词。纯本地运行，不上传任何文件。

![Python](https://img.shields.io/badge/python-3.11%2B-blue) ![License](https://img.shields.io/badge/license-MIT-green)

## 这个版本做什么

- 批量转换：FLAC / MP3 / M4A / OGG / WAV → MP3（V2 / V0 / 320）或 FLAC
- 标签：标题、歌手、专辑、音轨号、年份、歌词（内嵌 + 同名 `.lrc`）
- 封面：读取内嵌封面并写入输出文件，列表/专辑视图显示封面
- 队列：并发数可调、暂停/继续、失败重试、同名策略（重命名 / 覆盖 / 跳过）
- 界面：列表 / 专辑两种视图、搜索排序、五套配色、自定义强调色、可关动效
- 已转换历史（SQLite），输出文件被删掉后会自动回到"等待"

## 不做什么

**本开源版本不包含任何 DRM / 加密容器的解密实现。**

网易云 `.ncm`、QQ 音乐 `.qmc*` / `.mflac`、TME `.tm*`、酷狗 `.kgm` 等格式会被识别，
但标记为「加密格式：本开源版本不包含解密实现」，不做转换。这样它可以作为一个
干净的、可研究的**音频转换器 / 标签工具**公开使用。

关于加密容器的**原理性讨论**（不给密钥、不给可运行代码）见
[`docs/format-research.md`](docs/format-research.md)。

## 快速开始

```powershell
# 1. 准备 ffmpeg（首次，约 250 MB）
powershell -ExecutionPolicy Bypass -File tools\fetch-ffmpeg.ps1

# 2. 启动（需要 Python 3.11+，无任何 pip 依赖）
python -m app.server
```

浏览器自动打开。也可以双击 `start.bat`（无控制台）或 `start-debug.bat`（保留控制台）。

## 打包便携版

```powershell
python -m venv .venv-pack
.venv-pack\Scripts\pip install pyinstaller
.venv-pack\Scripts\pyinstaller.exe --noconfirm --clean --windowed --name Sonora ^
  --icon pack\icon.ico --paths . ^
  --add-data "web;web" --add-data "tools\ffmpeg;tools\ffmpeg" pack\launcher.py
```

产物 `dist/Sonora/` 是免安装版本，拷到任意 Windows 机器双击 `Sonora.exe` 即可。
首次运行会在 exe 同目录生成 `data/`（配置、历史、封面缓存）和 `output/`。

## 项目结构

```
app/       Python 后端：扫描、转码、标签读写、HTTP/SSE
web/       前端：原生 HTML/CSS/JS，本地字体，无外部请求
tools/     ffmpeg 放置目录（不提交二进制）
tests/     端到端测试
docs/      设计、API、格式研究（原理）
design/    图标
pack/      PyInstaller 启动器
```

## 测试

```powershell
python tests\test_plain.py
```

生成 4 种普通音频样本，跑完整扫描 / 转换 / 标签 / 封面流程，输出 `SUMMARY mp3=4/4 flac=4/4`。

## 说明

- 请只转换你合法拥有或有权处理的音频文件。
- 本项目不提供任何音乐内容，也不提供下载服务。
- 界面里的平台名称仅用于展示来源，不表示支持其加密格式。

## License

MIT（见 [LICENSE](LICENSE)）。第三方组件见 [THIRD_PARTY.md](THIRD_PARTY.md)。
