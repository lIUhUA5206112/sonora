# API 契约（M1）

本地服务 `http://127.0.0.1:<port>`。所有 `/api/*` 请求必须带 `X-Sonora-Token: <token>`（启动时生成，写入打开的 URL 的 `?t=`，前端存 sessionStorage）。静态页不需要。
返回统一为 `{"ok": true, "data": ...}` 或 `{"ok": false, "error": {"code": "...", "message": "..."}}`。
错误码：`UNSUPPORTED` `KEY_MISSING` `OUTPUT_LOCKED` `DISK_FULL` `FFMPEG_MISSING` `CORRUPT` `BAD_REQUEST` `FORBIDDEN` `NOT_FOUND`。

## GET /api/state

```json
{
  "ok": true,
  "data": {
    "version": "0.1.0",
    "ffmpeg": {"ok": true, "version": "N-128142"},
    "sources": [{"id": "a1b2c3", "path": "E:\\Music\\网易云", "count": 128, "scanning": false}],
    "output": {"dir": "E:\\Music\\Converted", "format": "mp3", "quality": "v2"},
    "settings": {"concurrency": 2, "autoScanOnStart": true, "openFolderWhenDone": false},
    "totals": {"all": 214, "waiting": 76, "decrypting": 1, "transcoding": 0, "done": 136, "failed": 1, "unsupported": 0},
    "batch": {"running": true, "paused": false, "percent": 64, "etaSec": 371, "ratePerMin": 12.6},
    "lastScanAt": 1794000000.0
  }
}
```

## GET /api/tracks?state=&source=&limit=200&offset=0

`state` ∈ `all|waiting|active|done|failed|unsupported`（`active` = decrypting/transcoding/tagging）。
按「转换中 → 失败 → 等待 → 已完成/不支持」排序。

```json
{"ok": true, "data": {"total": 214, "items": [{
  "id": "9f2c1e", "sourceId": "a1b2c3", "name": "七里香.ncm", "path": "E:\\Music\\网易云\\七里香.ncm",
  "size": 10240000, "fmt": "ncm", "platform": "网易云",
  "title": "七里香", "artist": "周杰伦", "album": "七里香", "durationMs": 299000,
  "state": "transcoding", "progress": 0.62, "error": null,
  "outputPath": null, "peaks": null, "hasCover": true
}]}}
```

`peaks`：100 个 0–100 的整数（有则前端画波形），否则 `null`。
`state` ∈ `waiting|decrypting|transcoding|tagging|done|failed|unsupported`。
`hasCover`：扫描时能确定有封面则为 true（NCM 读头部；其它格式读标签）。

## GET /api/cover/{id}?t=<token>

返回 96×96 JPEG 缩略图（首次请求时从源文件提取并用 ffmpeg 缩放，缓存在 `data/covers/`）。
没有封面返回 404；`<img>` 无法带请求头，所以这个接口同时接受 `?t=` 查询令牌。

## POST /api/pick-folder
`{"kind": "source"}` 或 `{"kind": "output"}` → 服务端弹出系统文件夹选择框。
`{"ok": true, "data": {"path": "E:\\..."}}`，用户取消则 `{"path": null}`。

## POST /api/sources
`{"paths": ["E:\\Music\\网易云"]}` → 添加并立即扫描（异步）。返回 `{"ok": true, "data": {"added": 1}}`。

## DELETE /api/sources/{id}
移除来源（不删文件），同时移除其下未转换的 track。

## POST /api/scan
`{"ids": []}`（空 = 全部来源）→ 重新扫描，异步。

## POST /api/convert
`{"ids": []}`（空 = 所有 waiting）→ 开始/继续转换。

## POST /api/pause  ·  POST /api/resume
批次级暂停/继续。正在转的文件会先完成。

## POST /api/tracks/{id}/retry  ·  POST /api/tracks/{id}/cancel
单首重试 / 取消。

## POST /api/open-output
`os.startfile(output.dir)`。

## PATCH /api/settings
```json
{"output": {"dir": "E:\\Out", "format": "flac", "quality": "v2"},
 "settings": {"concurrency": 2, "autoScanOnStart": true, "openFolderWhenDone": false,
              "autoRetry": true, "template": "{artist} - {title}", "onConflict": "rename"}}
```
只传要改的字段；`concurrency` 夹在 1–6（重启生效），`onConflict` ∈ `rename|overwrite|skip`。
返回更新后的 `state`。

## GET /api/apps
检测本机音乐应用的下载目录与已连接的手机。结果缓存 30 秒，`?refresh=1` 强制刷新。

```json
{"ok": true, "data": {
  "apps": [{"id": "netease", "name": "网易云音乐",
            "path": "E:\\CloudMusic\\VipSongsDownload", "exists": true, "count": 141}],
  "phone": {"devices": [{"name": "R5CT30XXXX", "transport": "adb"}],
            "hint": "把手机用数据线连上电脑，选「传输文件 / MTP」…", "adb": true}
}}
```

`id` ∈ `netease|qqmusic|kugou|tme`；网易云会优先取 `VipSongsDownload` 子目录。

## POST /api/apps/import
`{"id": "netease"}` → 把检测到的目录加为来源并开始扫描。`{"added": 1, "path": "..."}`。

## POST /api/phone/import
`{"serial": "<adb 序列号>"}` → 用 `adb pull` 把网易云 / QQ音乐 / 酷狗的常见下载目录拉到
`data/inbox/phone-<serial>/`，再加为来源。没有 adb 时返回 400 与中文原因。

## POST /api/upload?name=<文件名>
请求体 = 原始文件字节（最大 200MB）。写入 `data/inbox/`，把 inbox 加为来源并扫描。
`{"ok": true, "data": {"saved": 1}}`。前端拖放文件用这个（浏览器拿不到本地路径，只能复制）。

## GET /api/events （SSE，`text/event-stream`）

```
event: track
data: {"id":"9f2c1e","state":"transcoding","progress":0.62,"peaks":null,"error":null,"title":"七里香","artist":"周杰伦","durationMs":299000}

event: batch
data: {"running":true,"paused":false,"percent":64,"etaSec":371,"ratePerMin":12.6,"totals":{...}}

event: scan
data: {"running":true,"found":128,"done":false}

event: toast
data: {"level":"error","message":"ffmpeg 未找到"}
```
每 15 秒发一次 `: ping` 心跳。前端断线 3 秒后重连。

## 约定

- 所有路径是服务端绝对路径（Windows）。
- 时间戳用 Unix 秒（float）。
- 文件名模板 `{artist} - {title}`，空值回落到原文件名；冲突策略 `rename`（追加 ` (2)`）。
- 目标格式：`mp3`（quality `v2|v0|320`）或 `flac`。
- 只读源文件；输出写入 `output.dir`；临时文件在 `data/tmp/`。
