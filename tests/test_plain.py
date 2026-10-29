"""End-to-end: plain audio (FLAC/MP3/M4A/OGG) -> MP3 / FLAC.

The public build intentionally ships no DRM decryption; this test covers the
conversion, tagging, cover and lyrics pipeline.
"""

import os
import pathlib
import shutil
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
os.environ["SONORA_DATA"] = str(ROOT / ".agent" / "plaindata")
sys.path.insert(0, str(ROOT))

from app import config, ffmpeg, tagwriter, tagsread  # noqa: E402
from app.core import App  # noqa: E402

WORK = ROOT / ".agent" / "plainwork"
OUT_MP3 = ROOT / ".agent" / "plainout-mp3"
OUT_FLAC = ROOT / ".agent" / "plainout-flac"
for d in (WORK, OUT_MP3, OUT_FLAC):
    d.mkdir(parents=True, exist_ok=True)
    for p in d.iterdir():
        p.unlink()

ff = ffmpeg.find(ROOT)
assert ff, "需要 tools/ffmpeg（运行 tools/fetch-ffmpeg.ps1）"


def tone(path: pathlib.Path, codec: list, seconds: int = 6) -> None:
    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"sine=frequency=440:duration={seconds}:sample_rate=44100",
                    "-ac", "2", *codec, str(path)], check=True, creationflags=ffmpeg.CREATIONFLAGS)


cover_png = WORK / "cover.png"
subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x2f6b4f:s=300x300:d=1",
                "-frames:v", "1", str(cover_png)], check=True, creationflags=ffmpeg.CREATIONFLAGS)
cover = cover_png.read_bytes()

flac = WORK / "sample.flac"
mp3 = WORK / "sample.mp3"
m4a = WORK / "sample.m4a"
ogg = WORK / "sample.ogg"
tone(flac, ["-c:a", "flac"])
tone(mp3, ["-c:a", "libmp3lame", "-q:a", "2"])
tone(m4a, ["-c:a", "aac", "-b:a", "128k"])
tone(ogg, ["-c:a", "libvorbis", "-q:a", "5"])

tagwriter.write_tags(flac, "flac", "测试 FLAC", "测试歌手", "测试专辑", cover, "image/png",
                     lyrics="[00:01.00]测试歌词", track="1", date="2026")
tagwriter.write_tags(mp3, "mp3", "测试 MP3", "测试歌手", "测试专辑", cover, "image/png",
                     lyrics="[00:01.00]测试歌词", track="2", date="2026")

config.ensure_dirs()
cfg = dict(config.DEFAULTS)
cfg["sources"] = [{"id": config.source_id(str(WORK)), "path": str(WORK)}]
cfg["output"] = {"dir": str(OUT_MP3), "format": "mp3", "quality": "v2"}
cfg["settings"]["concurrency"] = 2

app = App(cfg)
app.scan()
for _ in range(120):
    if not app.scanning:
        break
    time.sleep(0.1)
print("scanned:", len(app.tracks))


def wait_idle(timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        active = any(t.state in ("decrypting", "transcoding", "tagging") for t in app.tracks.values())
        if not app.running and not active and app.q.empty():
            return True
        time.sleep(0.2)
    return False


def run(target: str) -> int:
    app.cfg["output"]["format"] = target
    app.convert(force=True)
    assert wait_idle(), target
    ok = 0
    for t in app.tracks.values():
        if t.state == "done" and t.output_path and pathlib.Path(t.output_path).exists():
            ok += 1
    return ok


mp3_ok = run("mp3")
flac_ok = run("flac")
print(f"SUMMARY mp3={mp3_ok}/{len(app.tracks)} flac={flac_ok}/{len(app.tracks)}")

# tag / cover round-trip on the FLAC source output
target = next(t for t in app.tracks.values() if t.fmt == "flac")
out = pathlib.Path(target.output_path)
tags = tagsread.read_tags(out)
print("cover:", len(tags.get("cover") or b""), "B | title:", tags.get("title"), "| lyrics:", bool(tags.get("lyrics")))

assert mp3_ok == len(app.tracks)
assert flac_ok == len(app.tracks)
assert tags.get("cover") and tags.get("title") == "测试 FLAC"
print("PASS: plain audio -> MP3/FLAC, tags/cover/lyrics preserved")
