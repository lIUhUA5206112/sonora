"""File format sniffing. Extension is a hint; magic bytes / tail decide."""

from __future__ import annotations

from pathlib import Path

# fmt -> (platform, decodable in M1, reason when not)
SUPPORTED_EXT = {
    ".ncm", ".flac", ".mp3", ".ogg", ".m4a", ".wav",
    ".qmc0", ".qmc2", ".qmc3", ".qmc4", ".qmc6", ".qmc8", ".qmcflac", ".qmcogg",
    ".mflac", ".mflac0", ".mflach", ".mgg", ".mgg0", ".mgg1", ".mggl",
    ".tm0", ".tm2", ".tm3", ".tm6", ".tkm",
    ".kgm", ".kgma", ".vpr", ".kgg", ".kwm", ".mg3d", ".xm",
}

PLATFORM = {
    "ncm": "网易云",
    "qmc": "QQ音乐",
    "tme": "QQ音乐",
    "kgm": "酷狗",
    "kwm": "酷我",
    "migu": "咪咕",
    "xm": "虾米",
    "flac": "本地", "mp3": "本地", "ogg": "本地", "m4a": "本地", "wav": "本地",
    "unknown": "未知",
}

EXT_FMT = {
    ".ncm": "ncm",
    ".flac": "flac", ".mp3": "mp3", ".ogg": "ogg", ".m4a": "m4a", ".wav": "wav",
    ".qmc0": "qmc", ".qmc2": "qmc", ".qmc3": "qmc", ".qmc4": "qmc", ".qmc6": "qmc",
    ".qmc8": "qmc", ".qmcflac": "qmc", ".qmcogg": "qmc",
    ".mflac": "qmc", ".mflac0": "qmc", ".mflach": "qmc",
    ".mgg": "qmc", ".mgg0": "qmc", ".mgg1": "qmc", ".mggl": "qmc",
    ".tm0": "tme", ".tm2": "tme", ".tm3": "tme", ".tm6": "tme", ".tkm": "tme",
    ".kgm": "kgm", ".kgma": "kgm", ".vpr": "kgm", ".kgg": "kgm",
    ".kwm": "kwm", ".mg3d": "migu", ".xm": "xm",
}

NEXT_STAGE = {
    "qmc": "QQ音乐格式将在 M2 支持",
    "tme": "TME 格式将在 M2 支持",
    "kgm": "酷狗格式将在 M2 支持",
    "kwm": "酷我格式暂不支持",
    "migu": "咪咕格式暂不支持",
    "xm": "虾米格式已停止支持",
    "unknown": "无法识别的文件格式",
}


def sniff(path: str) -> dict:
    p = Path(path)
    ext = p.suffix.lower()
    fmt = EXT_FMT.get(ext, "")
    head = b""
    try:
        with open(p, "rb") as fh:
            head = fh.read(16)
    except OSError:
        pass

    if head[:8] == b"CTENFDAM":
        fmt = "ncm"
    elif not fmt:
        if head[:4] == b"fLaC":
            fmt = "flac"
        elif head[:3] == b"ID3" or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
            fmt = "mp3"
        elif head[:4] == b"OggS":
            fmt = "ogg"
        elif head[4:8] == b"ftyp":
            fmt = "m4a"
        elif head[:4] == b"RIFF":
            fmt = "wav"
        else:
            fmt = "unknown"

    supported = fmt in ("flac", "mp3", "ogg", "m4a", "wav")
    reason = "" if supported else NEXT_STAGE.get(fmt, "暂不支持")
    if ext == ".mflach":
        supported = False
        reason = "mflach 暂不支持"
    elif ext == ".kgg":
        supported = False
        reason = "加密格式：本开源版本不包含解密实现"
    return {
        "fmt": fmt,
        "platform": PLATFORM.get(fmt, "未知"),
        "supported": supported,
        "reason": reason,
    }


def is_candidate(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in SUPPORTED_EXT and path.stat().st_size > 0
