# 设计概览

## 目标

一个**纯本地**的音频转换与标签工具：

- 输入：FLAC / MP3 / M4A / OGG / WAV
- 输出：MP3（V2 / V0 / 320）或 FLAC
- 保留：标题、歌手、专辑、音轨号、年份、封面、歌词

加密容器（NCM / QMC / TME / KGM 等）只做**识别**并标记为不支持；
本仓库不包含任何解密实现。

## 架构

```
浏览器 (web/)
   │  HTTP + SSE（localhost，token 鉴权）
   ▼
app/server.py   轻量 HTTP 服务、路由、SSE 广播
app/core.py     扫描、队列、并发转码、历史恢复
app/ffmpeg.py   调用 ffmpeg/ffprobe，解析进度
app/tagsread.py 读 ID3v2 / FLAC VorbisComment / 封面 / LRC
app/tagwriter.py 写 ID3v2.4 / FLAC（VorbisComment + PICTURE）
app/history.py  SQLite：path + mtime + size + target → 输出
web/            原生 HTML/CSS/JS，本地字体，无外部请求
```

## 扫描与转换流程

```
扫描目录 → 识别格式（魔数 / 标签）→ 读取元数据 → 生成任务
   → 转码（ffmpeg，带进度）→ 写标签/封面/歌词 → 原子改名到输出 → 完成
```

- 扫描在后台线程进行，SSE 推送进度
- 已转换记录按「路径 + 修改时间 + 大小 + 目标格式」命中，重启后直接显示"已转换"
- 输出文件被删除后，下次扫描自动回到"等待"

## 安全与隐私

- 服务只监听 `127.0.0.1`，随机端口 + 每次启动生成访问令牌
- 不上传任何文件，不访问外部网络（字体、图标全部本地）
- 关闭浏览器标签后，服务在宽限期后自动退出

## 界面

- 列表视图：勾选、排序、搜索、来源筛选、进度条、波形
- 专辑视图：按专辑名分组、封面网格、专辑内按音轨号排序
- 主题：5 套配色 + 自定义强调色；动效可关闭、速度/幅度可调
