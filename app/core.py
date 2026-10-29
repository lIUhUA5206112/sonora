"""Core state, scanning and the conversion pipeline."""

from __future__ import annotations

import os
import queue
import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import apps as apps_detect
from . import config, ffmpeg, history, log, sniff, tagwriter, tagsread

ACTIVE_STATES = ("decrypting", "transcoding", "tagging")
STATE_RANK = {"decrypting": 0, "transcoding": 0, "tagging": 0, "failed": 1,
              "waiting": 2, "done": 3, "unsupported": 4}


def _track_int(value) -> int:
    try:
        digits = "".join(ch for ch in str(value or "").split("/")[0] if ch.isdigit())
        return int(digits) if digits else 0
    except (TypeError, ValueError):
        return 0


@dataclass
class Track:
    id: str
    source_id: str
    path: str
    name: str
    size: int
    mtime_ns: int
    fmt: str
    platform: str
    supported: bool
    reason: str
    title: str = ""
    artist: str = ""
    album: str = ""
    track_no: int = 0
    duration_ms: int = 0
    state: str = "waiting"
    progress: float = 0.0
    error: str | None = None
    output_path: str | None = None
    peaks: list[int] | None = None
    has_cover: bool = False
    reconvert: bool = False
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "sourceId": self.source_id,
            "name": self.name,
            "path": self.path,
            "size": self.size,
            "fmt": self.fmt,
            "platform": self.platform,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "trackNo": self.track_no,
            "durationMs": self.duration_ms,
            "state": self.state,
            "progress": round(self.progress, 4),
            "error": self.error,
            "outputPath": self.output_path,
            "peaks": self.peaks,
            "hasCover": self.has_cover,
        }


def _clean(name: str, fallback: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name or "").strip(" .")
    return (name or fallback)[:160]


class App:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.lock = threading.RLock()
        self.tracks: dict[str, Track] = {}
        self.subs: list[queue.Queue] = []
        self.token = ""
        self.running = False
        self.paused = False
        self.scanning = False
        self.last_scan = 0.0
        self.started_at = 0.0
        self.done_times: list[float] = []
        self.q: queue.Queue[str] = queue.Queue()
        self._last_emit: dict[str, float] = {}
        self._last_batch = 0.0
        self.last_seen = time.time()
        self.bye_at = 0.0
        self.stop = threading.Event()
        self._apps_cache: dict | None = None
        self._apps_at = 0.0
        self.batch_ids: set[str] = set()

        self.ffmpeg = ffmpeg.find(config.ROOT)
        self.ffprobe = ffmpeg.probe_tool(self.ffmpeg) if self.ffmpeg else None
        self.ffmpeg_version = ffmpeg.version(self.ffmpeg) if self.ffmpeg else ""

        workers = max(1, min(6, int(cfg["settings"].get("concurrency", 2))))
        for _ in range(workers):
            threading.Thread(target=self._worker, daemon=True).start()
        threading.Thread(target=self._ticker, daemon=True).start()

    # ------------------------------------------------------------------ state
    def totals(self) -> dict:
        counts = {"all": 0, "waiting": 0, "decrypting": 0, "transcoding": 0,
                  "tagging": 0, "done": 0, "failed": 0, "unsupported": 0}
        for t in self.tracks.values():
            counts["all"] += 1
            counts[t.state] = counts.get(t.state, 0) + 1
        return counts

    def rate_per_min(self) -> float:
        cutoff = time.time() - 60
        recent = [x for x in self.done_times if x >= cutoff]
        if self.started_at and time.time() - self.started_at < 60:
            span = max(1.0, time.time() - self.started_at)
        else:
            span = 60.0
        return round(len(recent) * 60.0 / span, 1)

    def batch(self) -> dict:
        counts = self.totals()
        usable = counts["all"] - counts["unsupported"]
        with self.lock:
            if self.batch_ids:
                scope = [self.tracks[i] for i in self.batch_ids if i in self.tracks]
            else:
                scope = [t for t in self.tracks.values() if t.supported]
            total = len(scope)
            progress = sum(
                1.0 if t.state == "done" else (t.progress if t.state in ("decrypting", "transcoding", "tagging") else 0.0)
                for t in scope
            )
        percent = round(progress * 100 / total) if total else 0
        rate = self.rate_per_min()
        remaining = max(0.0, total - progress)
        eta = round(remaining * 60 / rate) if rate > 0 else 0
        return {
            "running": self.running,
            "paused": self.paused,
            "percent": percent,
            "etaSec": eta,
            "ratePerMin": rate,
            "totals": counts,
        }

    def state(self) -> dict:
        with self.lock:
            sources = []
            for s in self.cfg["sources"]:
                sources.append({
                    "id": s["id"],
                    "path": s["path"],
                    "count": sum(1 for t in self.tracks.values() if t.source_id == s["id"]),
                    "scanning": self.scanning,
                })
            return {
                "version": config.VERSION,
                "ffmpeg": {"ok": bool(self.ffmpeg), "version": self.ffmpeg_version},
                "sources": sources,
                "output": dict(self.cfg["output"]),
                "settings": dict(self.cfg["settings"]),
                "totals": self.totals(),
                "batch": self.batch(),
                "lastScanAt": self.last_scan,
                "ui": dict(self.cfg.get("ui") or {}),
            }

    def tracks_json(self, flt: str = "all", limit: int = 500, offset: int = 0) -> dict:
        with self.lock:
            items = list(self.tracks.values())
        if flt == "active":
            items = [t for t in items if t.state in ACTIVE_STATES]
        elif flt in ("waiting", "done", "failed", "unsupported"):
            items = [t for t in items if t.state == flt]
        items.sort(key=lambda t: (STATE_RANK.get(t.state, 9), t.name.lower()))
        total = len(items)
        page = items[offset:offset + limit]
        return {"total": total, "items": [t.to_dict() for t in page]}

    # ------------------------------------------------------------------ events
    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=300)
        with self.lock:
            self.subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            if q in self.subs:
                self.subs.remove(q)

    def _broadcast(self, event: str, payload: dict) -> None:
        for q in list(self.subs):
            try:
                q.put_nowait((event, payload))
            except queue.Full:
                pass

    def update(self, t: Track, **fields) -> None:
        with self.lock:
            before = t.state
            for key, value in fields.items():
                setattr(t, key, value)
            payload = {"id": t.id, "state": t.state, "progress": round(t.progress, 4),
                       "title": t.title, "artist": t.artist, "trackNo": t.track_no, "durationMs": t.duration_ms,
                       "error": t.error, "peaks": t.peaks, "outputPath": t.output_path}
            now = time.time()
            state_changed = t.state != before
            if state_changed or now - self._last_emit.get(t.id, 0) >= 0.08:
                self._last_emit[t.id] = now
                self._broadcast("track", payload)
        self._emit_batch()

    def _emit_batch(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_batch < 0.4:
            return
        self._last_batch = now
        self._broadcast("batch", self.batch())

    # ------------------------------------------------------------------ sources
    def add_source(self, path: str) -> str:
        path = os.path.abspath(path)
        if not os.path.isdir(path):
            raise ValueError(f"不是文件夹：{path}")
        sid = config.source_id(path)
        with self.lock:
            if not any(s["id"] == sid for s in self.cfg["sources"]):
                self.cfg["sources"].append({"id": sid, "path": path})
                config.save(self.cfg)
        self.scan([sid])
        return sid

    def remove_source(self, sid: str) -> None:
        with self.lock:
            self.cfg["sources"] = [s for s in self.cfg["sources"] if s["id"] != sid]
            config.save(self.cfg)
            for tid in [t.id for t in self.tracks.values() if t.source_id == sid]:
                self.tracks.pop(tid, None)
        self._emit_batch()

    # ------------------------------------------------------------------ scan
    @staticmethod
    def _fast_meta(path: str, fmt: str) -> dict:
        """Cheap metadata for the queue: NCM header, or the first 256 KB of tags."""
        title = artist = album = ""
        duration = 0
        track_no = 0
        has_cover = False
        try:
            if fmt in ("flac", "mp3", "ogg", "m4a", "wav"):
                tags = tagsread.read_tags(path, limit=262_144)
                title = (tags.get("title") or "").strip()
                artist = (tags.get("artist") or "").strip()
                album = (tags.get("album") or "").strip()
                has_cover = bool(tags.get("cover"))
                track_no = _track_int(tags.get("track"))
        except Exception:
            pass
        return {"title": title, "artist": artist, "album": album,
                "duration": duration, "track_no": track_no, "has_cover": has_cover}

    def scan(self, ids: list[str] | None = None) -> bool:
        with self.lock:
            if self.scanning:
                return False
            self.scanning = True
        sids = ids or [s["id"] for s in self.cfg["sources"]]
        threading.Thread(target=self._scan_worker, args=(sids,), daemon=True).start()
        return True

    def _scan_worker(self, sids: list[str]) -> None:
        found_total = 0
        try:
            for s in list(self.cfg["sources"]):
                if s["id"] not in sids:
                    continue
                found: dict[str, Track] = {}
                for root, dirs, files in os.walk(s["path"]):
                    dirs[:] = [d for d in dirs if not d.startswith(".")]
                    for name in files:
                        p = Path(root) / name
                        try:
                            if not sniff.is_candidate(p):
                                continue
                            st = p.stat()
                        except OSError:
                            continue
                        tid = config.track_id(str(p), st.st_size, st.st_mtime_ns)
                        with self.lock:
                            old = self.tracks.get(tid)
                        if old is not None:
                            # 输出文件被删/移走时，别继续显示"已转换"
                            if old.state == "done" and (not old.output_path or not Path(old.output_path).is_file()):
                                with self.lock:
                                    old.state = "waiting"
                                    old.progress = 0.0
                                    old.output_path = None
                                    old.peaks = []
                                    old.error = None
                                history.forget(old.path)
                            found[tid] = old
                            continue
                        info = sniff.sniff(str(p))
                        meta = self._fast_meta(str(p), info["fmt"])
                        track = Track(
                            id=tid, source_id=s["id"], path=str(p), name=name,
                            size=st.st_size, mtime_ns=st.st_mtime_ns,
                            fmt=info["fmt"], platform=info["platform"],
                            supported=info["supported"], reason=info["reason"],
                            title=meta["title"] or p.stem,
                            artist=meta["artist"],
                            album=meta["album"],
                            track_no=meta.get("track_no", 0),
                            duration_ms=meta["duration"],
                            has_cover=meta["has_cover"],
                            state="waiting" if info["supported"] else "unsupported",
                            error=None if info["supported"] else info["reason"],
                        )
                        if info["supported"]:
                            hit = history.get(str(p), st.st_mtime_ns, st.st_size,
                                              self._target_key())
                            if hit and Path(hit[0]).exists():
                                track.state = "done"
                                track.progress = 1.0
                                track.output_path = hit[0]
                                track.peaks = hit[1]
                        found[tid] = track
                        found_total += 1
                        if found_total % 25 == 0:
                            self._broadcast("scan", {"running": True, "found": found_total, "done": False})
                with self.lock:
                    for tid in [t.id for t in self.tracks.values() if t.source_id == s["id"]]:
                        if tid not in found:
                            self.tracks.pop(tid, None)
                    self.tracks.update(found)
                self._emit_batch()
            with self.lock:
                self.last_scan = time.time()
        finally:
            with self.lock:
                self.scanning = False
            self._broadcast("scan", {"running": False, "found": found_total, "done": True})
            self._broadcast("batch", self.batch())

    # ------------------------------------------------------------------ jobs
    def convert(self, ids: list[str] | None = None, force: bool = False) -> int:
        with self.lock:
            if ids:
                targets = [self.tracks[i] for i in ids if i in self.tracks]
                force = True
            else:
                targets = list(self.tracks.values())
            targets = [t for t in targets if t.supported and (force or t.state in ("waiting", "failed"))]
            if not targets:
                return 0
            self.batch_ids = {t.id for t in targets}
            self.running = True
            self.paused = False
            if not self.started_at or time.time() - self.started_at > 600:
                self.started_at = time.time()
                self.done_times = []
            for t in targets:
                t.reconvert = t.state == "done"
                t.state = "waiting"
                t.error = None
                t.progress = 0.0
                t.cancel.clear()
                self.q.put(t.id)
        self._emit_batch()
        return len(targets)

    def pause(self) -> None:
        self.paused = True
        self._emit_batch()

    def resume(self) -> None:
        self.paused = False
        self._emit_batch()

    def retry(self, tid: str) -> bool:
        with self.lock:
            t = self.tracks.get(tid)
            if not t:
                return False
            t.state = "waiting"
            t.error = None
            t.progress = 0.0
            t.cancel.clear()
            self.running = True
            self.paused = False
            self.q.put(tid)
        self._emit_batch()
        return True

    def cancel(self, tid: str) -> bool:
        with self.lock:
            t = self.tracks.get(tid)
            if not t:
                return False
            t.cancel.set()
            if t.state == "waiting":
                t.state = "waiting"
                t.progress = 0.0
        return True

    def _worker(self) -> None:
        while True:
            tid = self.q.get()
            if tid is None:
                return
            while self.paused:
                time.sleep(0.2)
            with self.lock:
                t = self.tracks.get(tid)
                if t is None or t.state not in ("waiting", "failed"):
                    continue
                t.state = "decrypting"
            self._pipeline(t)

    def _ticker(self) -> None:
        while True:
            time.sleep(0.7)
            finished = False
            with self.lock:
                active = any(t.state in ACTIVE_STATES for t in self.tracks.values())
                pending = not self.q.empty()
                if self.running and not active and not pending:
                    self.running = False
                    self.paused = False
                    finished = True
                    if self.cfg["settings"].get("openFolderWhenDone"):
                        self.open_output()
            self._emit_batch(force=finished)

    # ------------------------------------------------------------------ pipeline
    def _pipeline(self, t: Track) -> None:
        target = self.cfg["output"]["format"]
        quality = self.cfg["output"]["quality"]
        tmp_paths: list[Path] = []
        try:
            self.update(t, state="decrypting", progress=0.01, error=None)
            src_path = t.path
            src_fmt = t.fmt
            meta: dict = {}
            cover = b""
            mime = "image/jpeg"

            src_tags = tagsread.read_tags(src_path)
            title = (meta.get("musicName") or src_tags.get("title") or t.title or Path(t.name).stem).strip()
            artists = meta.get("artist") or []
            artist = "/".join(a[0] for a in artists if isinstance(a, list) and a and a[0])
            artist = artist or (src_tags.get("artist") or "")
            album = (meta.get("album") or src_tags.get("album") or "").strip()
            lyrics = src_tags.get("lyrics") or tagsread.sibling_lrc(t.path)
            track_no = src_tags.get("track") or ""
            date = src_tags.get("date") or ""
            if not cover and src_tags.get("cover"):
                cover = src_tags["cover"]
                mime = src_tags.get("cover_mime") or "image/jpeg"
            dur = int(meta.get("duration") or 0) or t.duration_ms
            if not dur and self.ffprobe:
                dur = ffmpeg.duration_ms(self.ffprobe, src_path)
            self.update(t, title=title, artist=artist, album=album, duration_ms=dur,
                        has_cover=bool(cover), progress=0.47)

            if t.peaks is None and self.ffmpeg:
                pk = ffmpeg.peaks(self.ffmpeg, src_path)
                if pk:
                    self.update(t, peaks=pk)
            self.update(t, state="transcoding", progress=0.5)

            out_dir = Path(self.cfg["output"]["dir"])
            out_dir.mkdir(parents=True, exist_ok=True)
            name = self._output_name(title, artist, fallback=Path(t.name).stem, target=target)
            out_path = out_dir / name
            conflict = "overwrite" if t.reconvert else self.cfg["settings"].get("onConflict", "rename")
            if out_path.exists():
                if conflict == "skip":
                    history.put(t.path, t.mtime_ns, t.size, self._target_key(),
                                str(out_path), t.peaks)
                    self.update(t, state="done", progress=1.0, output_path=str(out_path))
                    return
                if conflict == "rename":
                    out_path = self._unique_path(out_path)
            tmp_out = config.TMP / f"{t.id}-out.{target}"
            tmp_paths.append(tmp_out)

            if src_fmt == target:
                shutil.copyfile(src_path, tmp_out)
                self.update(t, progress=0.92)
            else:
                if not self.ffmpeg:
                    raise RuntimeError("未找到 ffmpeg，无法转码")
                ffmpeg.transcode(
                    self.ffmpeg, src_path, str(tmp_out), target, quality, dur,
                    lambda p: self.update(t, progress=0.5 + 0.43 * p), t.cancel,
                )
                self.update(t, progress=0.95)

            if src_fmt != target:
                tagwriter.write_tags(tmp_out, target, title, artist, album, cover, mime,
                                     lyrics, track_no, date, src_tags.get("extra"))
            self.update(t, state="tagging", progress=0.97)
            if t.cancel.is_set():
                raise RuntimeError("已取消")
            for attempt in range(3):
                try:
                    if out_path.exists():
                        out_path.unlink()  # 重转时覆盖旧文件，避免生成 " (2)" 副本
                    shutil.move(str(tmp_out), str(out_path))
                    break
                except OSError:
                    if attempt == 2:
                        raise
                    time.sleep(1.5 * (attempt + 1))  # 文件被占用时重试
            history.put(t.path, t.mtime_ns, t.size, self._target_key(), str(out_path), t.peaks)
            t.reconvert = False
            self.done_times.append(time.time())
            self.update(t, state="done", progress=1.0, output_path=str(out_path))
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI
            if t.cancel.is_set():
                self.update(t, state="waiting", progress=0.0, error=None)
            else:
                log.write(f"FAIL {t.name}: {exc}")
                self.update(t, state="failed", progress=0.0, error=str(exc)[:300])
        finally:
            for p in tmp_paths:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass

    def _output_name(self, title: str, artist: str, fallback: str, target: str) -> str:
        template = self.cfg["settings"].get("template") or "{artist} - {title}"
        try:
            base = template.format(artist=artist or "", title=title or "").strip(" -")
        except Exception:
            base = ""
        base = _clean(base, fallback)
        return f"{base}.{target}"

    @staticmethod
    def _unique_path(path: Path) -> Path:
        if not path.exists():
            return path
        stem, suffix = path.stem, path.suffix
        for i in range(2, 1000):
            cand = path.with_name(f"{stem} ({i}){suffix}")
            if not cand.exists():
                return cand
        raise RuntimeError("输出目录里同名文件过多")

    # ------------------------------------------------------------------ misc
    def _target_key(self) -> str:
        out = self.cfg["output"]
        if out["format"] == "flac":
            return "flac"
        return f"mp3:{out.get('quality', 'v2')}"

    # ------------------------------------------------------------- lifecycle
    def touch(self) -> None:
        """Any authenticated request proves a page is still open."""
        with self.lock:
            self.last_seen = time.time()
            self.bye_at = 0.0

    def bye(self) -> None:
        """The page is closing; the watchdog gives it a few seconds to be a reload."""
        with self.lock:
            self.bye_at = time.time()

    def output_file(self, tid: str) -> Path | None:
        with self.lock:
            t = self.tracks.get(tid)
            if not t or t.state != "done" or not t.output_path:
                return None
            p = Path(t.output_path)
        return p if p.is_file() else None

    def track_sig(self, tid: str) -> dict | None:
        with self.lock:
            t = self.tracks.get(tid)
        return {"mtime_ns": t.mtime_ns} if t else None

    def cover_data(self, tid: str) -> tuple[bytes, str] | None:
        with self.lock:
            t = self.tracks.get(tid)
        if not t:
            return None
        try:
            tags = tagsread.read_tags(t.path)
            if tags.get("cover"):
                return tags["cover"], tags.get("cover_mime") or "image/jpeg"
        except Exception:
            return None
        return None

    def open_output(self) -> None:
        d = self.cfg["output"]["dir"]
        Path(d).mkdir(parents=True, exist_ok=True)
        os.startfile(d)  # noqa: S606 - intentional on Windows

    def update_settings(self, payload: dict) -> dict:
        with self.lock:
            if "output" in payload:
                out = payload["output"]
                if "dir" in out and out["dir"]:
                    self.cfg["output"]["dir"] = os.path.abspath(out["dir"])
                    Path(self.cfg["output"]["dir"]).mkdir(parents=True, exist_ok=True)
                if out.get("format") in config.VALID_FORMATS:
                    self.cfg["output"]["format"] = out["format"]
                if out.get("quality") in config.VALID_QUALITY:
                    self.cfg["output"]["quality"] = out["quality"]
            if "settings" in payload:
                s = payload["settings"]
                for key in ("autoScanOnStart", "openFolderWhenDone", "autoRetry"):
                    if key in s:
                        self.cfg["settings"][key] = bool(s[key])
                if "template" in s:
                    text = str(s["template"]).strip()
                    self.cfg["settings"]["template"] = text or "{artist} - {title}"
                if "onConflict" in s and s["onConflict"] in ("rename", "overwrite", "skip"):
                    self.cfg["settings"]["onConflict"] = s["onConflict"]
                if "concurrency" in s:
                    try:
                        self.cfg["settings"]["concurrency"] = max(1, min(6, int(s["concurrency"])))
                    except (TypeError, ValueError):
                        pass
            if "ui" in payload and isinstance(payload["ui"], dict):
                u = payload["ui"]
                cur = self.cfg.setdefault("ui", dict(config.DEFAULTS["ui"]))
                if u.get("theme") in ("ink", "navy", "forest", "porcelain", "sand"):
                    cur["theme"] = u["theme"]
                if u.get("density") in ("compact", "standard", "comfy"):
                    cur["density"] = u["density"]
                for key in ("motion", "random"):
                    if isinstance(u.get(key), bool):
                        cur[key] = u[key]
                if u.get("speed") in ("slow", "normal", "fast"):
                    cur["speed"] = u["speed"]
                if u.get("view") in ("list", "albums"):
                    cur["view"] = u["view"]
                for key in ("showWaiting", "showActive"):
                    if isinstance(u.get(key), bool):
                        cur[key] = u[key]
                if u.get("amp") in ("sm", "md", "lg"):
                    cur["amp"] = u["amp"]
                if isinstance(u.get("accent"), str):
                    acc = u["accent"].strip()
                    if not acc or (len(acc) == 7 and acc[0] == "#"
                                   and all(c in "0123456789abcdefABCDEF" for c in acc[1:])):
                        cur["accent"] = acc
            config.save(self.cfg)
        self._emit_batch()
        return self.state()

    def apps_report(self, force: bool = False) -> dict:
        with self.lock:
            if not force and self._apps_cache is not None and time.time() - self._apps_at < 30:
                return self._apps_cache
        data = {"apps": apps_detect.detect_pc(), "phone": apps_detect.detect_phone()}
        with self.lock:
            self._apps_cache = data
            self._apps_at = time.time()
        return data
