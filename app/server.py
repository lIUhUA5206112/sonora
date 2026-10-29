"""Local HTTP server + JSON API + SSE. Standard library only."""

from __future__ import annotations

import json
import mimetypes
import os
import queue
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import apps as apps_detect
from . import config, ffmpeg, log
from .core import App

CREATE_NO_WINDOW = 0x08000000
MAX_UPLOAD = 300 * 1024 * 1024

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".woff2": "font/woff2",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".ico": "image/x-icon",
}


def pick_folder(title: str = "选择文件夹") -> str | None:
    ps = (
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8;" + "Add-Type -AssemblyName System.Windows.Forms;"
        "$top = New-Object System.Windows.Forms.Form;"
        "$top.TopMost = $true; $top.ShowInTaskbar = $false; $top.FormBorderStyle = 'None';"
        "$top.WindowState = 'Minimized'; $top.Show();"
        f"$dlg = New-Object System.Windows.Forms.FolderBrowserDialog; $dlg.Description = '{title}';"
        "$dlg.ShowNewFolderButton = $true;"
        "$r = $dlg.ShowDialog($top);"
        "if ($r -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::Out.Write($dlg.SelectedPath) };"
        "$top.Close()"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", ps],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600, creationflags=CREATE_NO_WINDOW,
        )
        path = (out.stdout or "").strip()
        return path or None
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"无法打开文件夹选择框：{exc}") from exc


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Sonora/0.1"

    @property
    def app(self) -> App:
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, fmt, *args):  # keep the console quiet
        pass

    # ---------------------------------------------------------------- helpers
    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _ok(self, data) -> None:
        self._send_json({"ok": True, "data": data})

    def _err(self, code: str, message: str, status: int = 400) -> None:
        self._send_json({"ok": False, "error": {"code": code, "message": message}}, status)

    def _body(self) -> dict:
        raw = getattr(self, "_body_bytes", b"")
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _token_ok(self, query: dict) -> bool:
        header = self.headers.get("X-Sonora-Token") or ""
        qtok = (query.get("t") or [""])[0]
        return bool(self.app.token) and (secrets.compare_digest(header, self.app.token)
                                         or secrets.compare_digest(qtok, self.app.token))

    def _send_bytes(self, body: bytes, content_type: str, cache_seconds: int = 0) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", f"max-age={cache_seconds}" if cache_seconds else "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _cover(self, tid: str) -> None:
        covers = config.DATA / "covers"
        covers.mkdir(parents=True, exist_ok=True)
        cache = covers / f"{tid}.jpg"
        sig = covers / f"{tid}.sig"
        none = covers / f"{tid}.none"
        meta = self.app.track_sig(tid)
        if cache.exists() and sig.exists() and meta:
            try:
                if int(sig.read_text() or 0) == int(meta["mtime_ns"]):
                    self._send_bytes(cache.read_bytes(), "image/jpeg", 86400)
                    return
            except (OSError, ValueError):
                pass
        if none.exists() and not cache.exists():
            if meta and sig.exists():
                self._err("NOT_FOUND", "no cover", 404)
                return
        data = self.app.cover_data(tid)
        if not data:
            none.touch()
            self._err("NOT_FOUND", "no cover", 404)
            return
        cover, mime = data
        try:
            cache.write_bytes(cover)
            if meta:
                sig.write_text(str(meta["mtime_ns"]))
        except OSError:
            pass
        self._send_bytes(cover, mime, 86400)

    def _file(self, tid: str) -> None:
        p = self.app.output_file(tid)
        if not p:
            self._err("NOT_FOUND", "文件不存在或尚未转换", 404)
            return
        suffix = p.suffix.lower()
        mime = {".mp3": "audio/mpeg", ".flac": "audio/flac", ".m4a": "audio/mp4",
                ".ogg": "audio/ogg"}.get(suffix, "application/octet-stream")
        quoted = urllib.parse.quote(p.name)
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(p.stat().st_size))
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quoted}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            with open(p, "rb") as fh:
                while True:
                    chunk = fh.read(1 << 16)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _static(self, path: str) -> None:
        rel = path.lstrip("/") or "index.html"
        target = (config.WEB / rel).resolve()
        try:
            target.relative_to(config.WEB.resolve())
        except ValueError:
            self._err("NOT_FOUND", "not found", 404)
            return
        if not target.is_file():
            self._err("NOT_FOUND", f"missing static file: {rel}", 404)
            return
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(target.suffix.lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    # ---------------------------------------------------------------- routing
    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path.startswith("/api/"):
            if not self._token_ok(query):
                self._err("FORBIDDEN", "缺少或错误的访问令牌", 403)
                return
            self.app.touch()
            if path == "/api/ping":
                self._ok({"ok": True})
            elif path == "/api/bye":
                self.app.bye()
                self._ok({"ok": True})
            elif path == "/api/state":
                self._ok(self.app.state())
            elif path == "/api/tracks":
                flt = (query.get("state") or ["all"])[0]
                limit = int((query.get("limit") or ["500"])[0])
                offset = int((query.get("offset") or ["0"])[0])
                self._ok(self.app.tracks_json(flt, limit, offset))
            elif path == "/api/events":
                self._sse()
            elif path.startswith("/api/cover/"):
                self._cover(path.rsplit("/", 1)[-1])
            elif path.startswith("/api/file/"):
                self._file(path.rsplit("/", 1)[-1])
            elif path == "/api/apps":
                force = (query.get("refresh") or ["0"])[0] == "1"
                self._ok(self.app.apps_report(force=force))
            else:
                self._err("NOT_FOUND", path, 404)
            return

        self._static(path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        self._body_bytes = b""
        if path != "/api/upload":
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self._body_bytes = self.rfile.read(length)
        if not self._token_ok(query):
            self._err("FORBIDDEN", "缺少或错误的访问令牌", 403)
            return
        self.app.touch()
        try:
            if path == "/api/ping":
                self._ok({"ok": True})
            elif path == "/api/bye":
                self.app.bye()
                self._ok({"ok": True})
            elif path == "/api/apps/import":
                body = self._body()
                app_id = body.get("id") or ""
                wanted = (body.get("path") or "").lower()
                report = self.app.apps_report()
                match = None
                if wanted:
                    match = next((a for a in report["apps"] if a["path"].lower() == wanted), None)
                if not match:
                    match = next((a for a in report["apps"] if a["id"] == app_id), None)
                if not match:
                    raise ValueError("没有找到该应用的下载目录")
                self.app.add_source(match["path"])
                self._ok({"added": 1, "path": match["path"]})
            elif path == "/api/phone/import":
                body = self._body()
                res = apps_detect.pull_phone(str(body.get("serial") or ""))
                self.app.add_source(res["path"])
                self._ok(res)
            elif path == "/api/pick-folder":
                body = self._body()
                kind = body.get("kind", "source")
                title = "选择输出文件夹" if kind == "output" else "选择音乐文件夹"
                self._ok({"path": pick_folder(title)})
            elif path == "/api/sources":
                body = self._body()
                paths = [p for p in body.get("paths", []) if isinstance(p, str)]
                added = 0
                for p in paths:
                    self.app.add_source(p)
                    added += 1
                self._ok({"added": added})
            elif path == "/api/scan":
                body = self._body()
                started = self.app.scan(body.get("ids") or None)
                self._ok({"started": started})
            elif path == "/api/convert":
                body = self._body()
                queued = self.app.convert(body.get("ids") or None, bool(body.get("force")))
                self._ok({"queued": queued})
            elif path == "/api/pause":
                self.app.pause()
                self._ok(self.app.state())
            elif path == "/api/resume":
                self.app.resume()
                self._ok(self.app.state())
            elif path == "/api/open-output":
                self.app.open_output()
                self._ok({"opened": True})
            elif path == "/api/upload":
                self._upload(query)
            elif re.match(r"^/api/tracks/[^/]+/(retry|cancel)$", path):
                tid = path.split("/")[3]
                action = path.rsplit("/", 1)[-1]
                ok = self.app.retry(tid) if action == "retry" else self.app.cancel(tid)
                self._ok({"ok": ok})
            else:
                self._err("NOT_FOUND", path, 404)
        except Exception as exc:  # noqa: BLE001
            self._err("BAD_REQUEST", str(exc), 400)

    def do_PATCH(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/api/settings":
            self._err("NOT_FOUND", parsed.path, 404)
            return
        self._body_bytes = b""
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self._body_bytes = self.rfile.read(length)
        if not self._token_ok(urllib.parse.parse_qs(parsed.query)):
            self._err("FORBIDDEN", "缺少或错误的访问令牌", 403)
            return
        try:
            self._ok(self.app.update_settings(self._body()))
        except Exception as exc:  # noqa: BLE001
            self._err("BAD_REQUEST", str(exc), 400)

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        if not self._token_ok(urllib.parse.parse_qs(parsed.query)):
            self._err("FORBIDDEN", "缺少或错误的访问令牌", 403)
            return
        m = re.match(r"^/api/sources/([^/]+)$", parsed.path)
        if not m:
            self._err("NOT_FOUND", parsed.path, 404)
            return
        self.app.remove_source(m.group(1))
        self._ok({"removed": True})

    # ---------------------------------------------------------------- upload
    def _upload(self, query: dict) -> None:
        name = (query.get("name") or [""])[0]
        name = os.path.basename(name.replace("\\", "/")).strip() or f"upload-{int(time.time())}"
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_UPLOAD:
            self._err("BAD_REQUEST", "文件为空或超过 300MB", 400)
            return
        config.INBOX.mkdir(parents=True, exist_ok=True)
        dest = config.INBOX / name
        stem, suffix = dest.stem, dest.suffix
        i = 2
        while dest.exists():
            dest = config.INBOX / f"{stem} ({i}){suffix}"
            i += 1
        remaining = length
        with open(dest, "wb") as fh:
            while remaining > 0:
                chunk = self.rfile.read(min(1 << 20, remaining))
                if not chunk:
                    break
                fh.write(chunk)
                remaining -= len(chunk)
        self.app.add_source(str(config.INBOX))
        self._ok({"saved": 1, "name": dest.name})

    # ---------------------------------------------------------------- SSE
    def _sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        q = self.app.subscribe()
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                try:
                    event, payload = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                data = json.dumps(payload, ensure_ascii=False)
                self.wfile.write(f"event: {event}\ndata: {data}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.app.unsubscribe(q)


def _watchdog(app, httpd) -> None:
    """Exit when the page is gone: a beacon on close (4s grace) or a missed heartbeat."""
    if os.environ.get("SONORA_EXIT_ON_CLOSE", "1") == "0":
        return
    while True:
        time.sleep(1.5)
        now = time.time()
        if app.bye_at and now - app.bye_at > 7 and app.last_seen <= app.bye_at:
            break
        if now - app.last_seen > 25:
            break
    app.stop.set()
    try:
        httpd.shutdown()
    except Exception:
        pass


def run() -> None:
    config.ensure_dirs()
    if config.TMP.exists():
        for p in config.TMP.iterdir():
            if p.is_file():
                p.unlink(missing_ok=True)
    cfg = config.load()
    app = App(cfg)
    token = os.environ.get("SONORA_TOKEN") or secrets.token_urlsafe(24)
    app.token = token

    httpd = ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("SONORA_PORT", "0"))), Handler)
    httpd.daemon_threads = True
    httpd.app = app  # type: ignore[attr-defined]
    port = httpd.server_address[1]
    url = f"http://127.0.0.1:{port}/?t={token}"

    if sys.stdout is not None:  # pythonw 下 stdout 为 None
        print(f"拾音 Sonora {config.VERSION}")
        print(f"  ffmpeg : {app.ffmpeg or '未找到'}")
        print(f"  output : {cfg['output']['dir']}")
        print(f"  open   : {url}")
        print("  Ctrl+C 退出")
    log.write(f"start port={port}")

    if cfg["settings"].get("autoScanOnStart") and cfg["sources"]:
        threading.Thread(target=app.scan, daemon=True).start()
    if not os.environ.get("SONORA_NO_BROWSER"):
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    threading.Thread(target=_watchdog, args=(app, httpd), daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        log.write("stop")
        httpd.server_close()


if __name__ == "__main__":
    sys.exit(run())
