"""Detect local music-app download folders and connected phones.

PC: walks a short list of known app folders (and the root of each fixed drive)
    for NetEase / QQ Music / Kugou / TME download dirs, counting importable files.
Phone: lists adb devices and MTP devices; adb is also used to pull the apps'
    download folders into data/inbox (MTP cannot be copied through a path).
"""

from __future__ import annotations

import os
import shutil
import string
import subprocess
from pathlib import Path

from . import config, log

AUDIO_EXTS = {".ncm", ".qmc0", ".qmc2", ".qmc3", ".qmcflac", ".qmcogg",
              ".mflac", ".mflac0", ".mgg", ".mgg0", ".mgg1", ".mggl",
              ".tm0", ".tm2", ".tm3", ".tm6", ".tkm",
              ".kgm", ".kgma", ".vpr", ".flac", ".mp3", ".ogg", ".m4a", ".wav"}

APPS = [
    ("netease", "网易云音乐", ["Music/CloudMusic", "CloudMusic", "Documents/CloudMusic", "Music/Netease"]),
    ("qqmusic", "QQ音乐", ["Music/QQMusic", "QQMusic", "Documents/QQMusic"]),
    ("kugou", "酷狗音乐", ["Music/Kugou", "Kugou", "Music/KuGou"]),
    ("tme", "TME / 腾讯音乐", ["Music/TME", "TME", "Music/QQMusic/TME"]),
]

PHONE_DIRS = {
    "netease": ["/sdcard/NetEase/cloudmusic/Music",
                "/sdcard/Android/data/com.netease.cloudmusic/files/download"],
    "qqmusic": ["/sdcard/QQMusic/song", "/sdcard/qqmusic/song",
                "/sdcard/Android/data/com.tencent.qqmusic/files/qqmusic/song"],
    "kugou": ["/sdcard/kgmusic/download", "/sdcard/KuGou"],
}


def _roots() -> list[Path]:
    home = Path.home()
    roots = [home / "Music", home / "Documents", home / "Downloads", home]
    for letter in string.ascii_uppercase:
        drive = Path(f"{letter}:\\")
        if drive.exists():
            roots.append(drive)
    return roots


def _count(path: Path, cap: int = 20000) -> int:
    total = 0
    seen = 0
    try:
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for name in files:
                seen += 1
                if Path(name).suffix.lower() in AUDIO_EXTS:
                    total += 1
            if seen > cap:
                break
    except OSError:
        return total
    return total


def _song_dir(cand: Path) -> Path:
    vip = cand / "VipSongsDownload"
    try:
        if vip.is_dir():
            return vip
    except OSError:
        pass
    return cand


def detect_pc() -> list[dict]:
    found: dict[str, dict] = {}

    def add(app_id: str, app_name: str, cand: Path) -> None:
        try:
            if not cand.is_dir():
                return
        except OSError:
            return
        target = _song_dir(cand)
        try:
            key = str(target.resolve()).lower()
        except OSError:
            return
        if key in found:
            return
        count = _count(target)
        if count:
            found[key] = {"id": app_id, "key": f"{app_id}|{target}", "name": app_name,
                          "path": str(target), "exists": True, "count": count}

    for app_id, app_name, relatives in APPS:
        for root in _roots():
            for rel in relatives:
                add(app_id, app_name, root / rel)

    named = {"cloudmusic": ("netease", "网易云音乐"), "qqmusic": ("qqmusic", "QQ音乐"),
             "kugou": ("kugou", "酷狗音乐"), "tme": ("tme", "TME / 腾讯音乐")}
    for letter in string.ascii_uppercase:
        drive = Path(f"{letter}:\\")
        if not drive.exists():
            continue
        for name, (app_id, app_name) in named.items():
            add(app_id, app_name, drive / ("CloudMusic" if name == "cloudmusic" else name))
    return sorted(found.values(), key=lambda a: -a["count"])[:12]


def _ps(command: str, timeout: int = 20) -> str:
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True, timeout=timeout, creationflags=0x08000000,
        )
        return proc.stdout.decode("utf-8", "replace").strip()
    except Exception:
        return ""


def detect_phone() -> dict:
    devices: list[dict] = []
    adb = shutil.which("adb")
    if adb:
        out = _ps(f'& "{adb}" devices')
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                devices.append({"name": parts[0], "transport": "adb"})
    # MTP / portable devices: anything under "This PC" that is not a drive letter or a UNC path
    script = (
        "$s = New-Object -ComObject Shell.Application; "
        "$ns = $s.Namespace(17); "
        "$ns.Items() | ForEach-Object { "
        "if ($_.IsFolder -and $_.Path -notmatch '^[A-Za-z]:' -and $_.Path -notmatch '^\\\\\\\\') { $_.Name } }"
    )
    for name in _ps(script).splitlines():
        name = name.strip()
        if name and not any(d["name"] == name for d in devices):
            devices.append({"name": name, "transport": "mtp"})
    hint = ""
    if not devices:
        hint = "把手机用数据线连上电脑，选「传输文件 / MTP」，允许访问后重新检测。"
    elif all(d["transport"] == "mtp" for d in devices):
        hint = "已看到 MTP 设备。Windows 无法直接给出它的路径；若已装 adb，可一键拉取网易云/QQ音乐/酷狗的下载目录。"
    return {"devices": devices, "hint": hint, "adb": bool(adb)}


def pull_phone(serial: str) -> dict:
    adb = shutil.which("adb")
    if not adb:
        raise ValueError("未找到 adb，无法从手机拉取文件")
    dest = config.INBOX / f"phone-{serial[:12].replace(':', '-')}"
    dest.mkdir(parents=True, exist_ok=True)
    pulled = 0
    for app_id, dirs in PHONE_DIRS.items():
        for remote in dirs:
            check = subprocess.run([adb, "-s", serial, "shell", "ls", remote],
                                   capture_output=True, timeout=20,
                                   creationflags=0x08000000)
            if check.returncode != 0:
                continue
            target = dest / app_id
            target.mkdir(parents=True, exist_ok=True)
            subprocess.run([adb, "-s", serial, "pull", remote + "/.", str(target)],
                           capture_output=True, timeout=600,
                           creationflags=0x08000000)
            pulled += sum(1 for p in target.rglob("*") if p.is_file())
    log.write(f"phone pull {serial}: {pulled} files")
    return {"path": str(dest), "files": pulled}
