"""Paths, config persistence."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

VERSION = "0.4.0"

FROZEN = bool(getattr(__import__("sys"), "frozen", False))
if FROZEN:
    _exe_dir = Path(__import__("sys").executable).resolve().parent
    APP_DIR = Path(getattr(__import__("sys"), "_MEIPASS", _exe_dir))
    BASE_DIR = _exe_dir
else:
    APP_DIR = BASE_DIR = Path(__file__).resolve().parent.parent

ROOT = APP_DIR
DATA = Path(os.environ.get("SONORA_DATA") or (BASE_DIR / "data"))
TMP = DATA / "tmp"
INBOX = DATA / "inbox"
OUTPUT_DEFAULT = BASE_DIR / "output"
CONFIG_PATH = DATA / "config.json"
WEB = APP_DIR / "web"

DEFAULTS = {
    "sources": [],
    "output": {"dir": str(OUTPUT_DEFAULT), "format": "mp3", "quality": "v2"},
    "settings": {
        "concurrency": 2,
        "autoScanOnStart": True,
        "openFolderWhenDone": False,
        "template": "{artist} - {title}",
        "onConflict": "rename",
    },
    "ui": {
        "theme": "sand",
        "density": "standard",
        "motion": True,
        "speed": "normal",
        "amp": "md",
        "random": False,
        "accent": "",
        "view": "list",
        "showWaiting": False,
        "showActive": False,
    },
}

VALID_FORMATS = ("mp3", "flac")
VALID_QUALITY = ("v2", "v0", "320")


def norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def source_id(path: str) -> str:
    return hashlib.sha1(norm(path).encode("utf-8")).hexdigest()[:10]


def track_id(path: str, size: int, mtime_ns: int) -> str:
    raw = f"{norm(path)}|{size}|{mtime_ns}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:12]


def ensure_dirs() -> None:
    for d in (DATA, TMP, INBOX):
        d.mkdir(parents=True, exist_ok=True)


def _merge(base: dict, incoming: dict) -> dict:
    out = dict(base)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load() -> dict:
    ensure_dirs()
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            cfg = _merge(DEFAULTS, json.loads(CONFIG_PATH.read_text("utf-8")))
        except Exception:
            cfg = dict(DEFAULTS)
    # sanity
    out = cfg.setdefault("output", {})
    if out.get("format") not in VALID_FORMATS:
        out["format"] = "mp3"
    if out.get("quality") not in VALID_QUALITY:
        out["quality"] = "v2"
    if not out.get("dir"):
        out["dir"] = str(OUTPUT_DEFAULT)
    Path(out["dir"]).mkdir(parents=True, exist_ok=True)
    ui = cfg.setdefault("ui", {})
    for key, value in DEFAULTS["ui"].items():
        ui.setdefault(key, value)
    return cfg


def save(cfg: dict) -> None:
    ensure_dirs()
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")
