"""ffmpeg / ffprobe helpers: transcode with real progress, peaks, duration."""

from __future__ import annotations

import glob
import subprocess
import threading
from pathlib import Path
from typing import Callable

CREATIONFLAGS = 0x08000000  # CREATE_NO_WINDOW on Windows, avoids console flashes


def find(root: Path) -> str | None:
    for pattern in (
        str(root / "tools" / "ffmpeg" / "*" / "bin" / "ffmpeg.exe"),
        str(root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"),
    ):
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[-1]
    return None


def probe_tool(ffmpeg: str) -> str | None:
    ffprobe = Path(ffmpeg).with_name("ffprobe.exe")
    return str(ffprobe) if ffprobe.exists() else None


def version(ffmpeg: str) -> str:
    try:
        out = subprocess.run(
            [ffmpeg, "-version"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
            creationflags=CREATIONFLAGS,
        ).stdout
        first = out.splitlines()[0] if out else ""
        return first.replace("ffmpeg version ", "").split(" Copyright")[0].strip()
    except Exception:
        return "unknown"


def duration_ms(ffprobe: str, path: str) -> int:
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, creationflags=CREATIONFLAGS,
        ).stdout.strip()
        return int(float(out) * 1000)
    except Exception:
        return 0


def peaks(ffmpeg: str, path: str, buckets: int = 100) -> list[int] | None:
    """Decode to 8 kHz mono PCM and reduce to `buckets` normalised peaks (0-100)."""
    try:
        proc = subprocess.run(
            [ffmpeg, "-v", "error", "-nostdin", "-i", path,
             "-map", "0:a:0", "-f", "s16le", "-ac", "1", "-ar", "8000", "-"],
            capture_output=True, timeout=180, creationflags=CREATIONFLAGS,
        )
        data = proc.stdout
        if not data or len(data) < 2:
            return None
        count = len(data) // 2
        step = max(1, count // buckets)
        out: list[int] = []
        for i in range(buckets):
            start = i * step * 2
            end = min(len(data), (i + 1) * step * 2)
            if start >= end:
                out.append(0)
                continue
            chunk = data[start:end]
            peak = max(abs(int.from_bytes(chunk[j:j + 2], "little", signed=True))
                       for j in range(0, len(chunk) - 1, 2))
            out.append(peak)
        top = max(out) or 1
        return [max(3, round(v * 100 / top)) for v in out]
    except Exception:
        return None


class TranscodeError(RuntimeError):
    pass


def thumbnail(ffmpeg: str, src_bytes: bytes, dst: Path, size: int = 96) -> Path:
    """Write a square JPEG thumbnail from raw image bytes."""
    tmp = dst.with_name(dst.stem + ".src")
    tmp.write_bytes(src_bytes)
    try:
        proc = subprocess.run(
            [ffmpeg, "-v", "error", "-y", "-i", str(tmp),
             "-vf", f"scale={size}:{size}:force_original_aspect_ratio=increase,crop={size}:{size}",
             "-q:v", "4", str(dst)],
            capture_output=True, timeout=60, creationflags=CREATIONFLAGS,
        )
        if proc.returncode != 0 or not dst.exists():
            raise RuntimeError(proc.stderr.decode("utf-8", "replace")[:200])
        return dst
    finally:
        tmp.unlink(missing_ok=True)


def transcode(ffmpeg: str, src: str, dst: str, target: str, quality: str,
              duration_ms_hint: int, on_progress: Callable[[float], None] | None,
              cancel: threading.Event | None = None) -> None:
    args = [
        ffmpeg, "-hide_banner", "-nostdin", "-y",
        "-i", src, "-map", "0:a:0", "-map_metadata", "-1", "-vn", "-sn", "-dn",
        "-progress", "pipe:1", "-nostats", "-stats_period", "0.25",
    ]
    if target == "mp3":
        args += ["-c:a", "libmp3lame"]
        if quality == "v0":
            args += ["-q:a", "0"]
        elif quality == "320":
            args += ["-b:a", "320k"]
        else:
            args += ["-q:a", "2"]
    elif target == "flac":
        args += ["-c:a", "flac", "-compression_level", "8"]
    else:
        raise TranscodeError(f"不支持的目标格式：{target}")
    args.append(dst)

    proc = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=CREATIONFLAGS,
    )
    errors: list[str] = []

    def drain():
        assert proc.stderr is not None
        for raw in proc.stderr:
            line = raw.decode("utf-8", "replace").rstrip()
            if line:
                errors.append(line)
                if len(errors) > 40:
                    del errors[:20]

    t = threading.Thread(target=drain, daemon=True)
    t.start()

    total = duration_ms_hint / 1000.0 if duration_ms_hint else 0.0
    assert proc.stdout is not None
    for raw in proc.stdout:
        if cancel is not None and cancel.is_set():
            proc.terminate()
            break
        line = raw.decode("ascii", "replace").strip()
        if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
            try:
                us = int(line.split("=", 1)[1])
            except ValueError:
                continue
            if total > 0 and on_progress:
                on_progress(min(0.99, max(0.0, us / 1_000_000 / total)))
    proc.wait()
    t.join(timeout=2)
    if cancel is not None and cancel.is_set():
        raise TranscodeError("已取消")
    if proc.returncode != 0:
        tail = " | ".join(errors[-4:]) or f"exit {proc.returncode}"
        raise TranscodeError(f"ffmpeg 转码失败：{tail}")
    if on_progress:
        on_progress(1.0)
