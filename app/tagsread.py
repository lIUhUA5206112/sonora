"""Read tags (ID3v2.3/2.4, FLAC VorbisComment) without third-party libraries.

Used to (a) show real titles right after scanning, (b) carry lyrics / cover /
track / date across a transcode, and (c) feed the cover endpoint.
"""

from __future__ import annotations

from pathlib import Path

TEXT_FRAMES = {
    b"TIT2": "title",
    b"TPE1": "artist",
    b"TALB": "album",
    b"TRCK": "track",
    b"TYER": "date",
    b"TDRC": "date",
    b"TCOM": "composer",
}

FLAC_KEYS = {
    "TITLE": "title",
    "ARTIST": "artist",
    "ALBUM": "album",
    "TRACKNUMBER": "track",
    "DATE": "date",
    "LYRICS": "lyrics",
    "UNSYNCEDLYRICS": "lyrics",
    "LYRIC": "lyrics",
}

_SKIP = {"title", "artist", "album", "track", "date", "lyrics"}


def _decode_text(enc: int, data: bytes) -> str:
    try:
        if enc == 0:
            return data.decode("latin-1", "replace").rstrip("\x00")
        if enc == 1:
            if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
                return data.decode("utf-16", "replace").rstrip("\x00")
            return data.decode("utf-16-le", "replace").rstrip("\x00")
        if enc == 2:
            return data.decode("utf-16-be", "replace").rstrip("\x00")
        return data.decode("utf-8", "replace").rstrip("\x00")
    except Exception:
        return ""


def _cstr(data: bytes, enc: int, start: int) -> tuple[str, int]:
    if enc in (1, 2):
        i = start
        while i + 1 < len(data):
            if data[i:i + 2] == b"\x00\x00":
                return data[start:i].decode("utf-16-le", "replace"), i + 2
            i += 2
        return data[start:].decode("utf-16-le", "replace"), len(data)
    i = data.find(b"\x00", start)
    if i < 0:
        return data[start:].decode("latin-1", "replace"), len(data)
    return data[start:i].decode("latin-1", "replace"), i + 1


def _syncsafe(b: bytes) -> int:
    return ((b[0] & 0x7F) << 21) | ((b[1] & 0x7F) << 14) | ((b[2] & 0x7F) << 7) | (b[3] & 0x7F)


def _parse_id3(data: bytes) -> dict:
    out: dict = {}
    if len(data) < 10 or data[:3] != b"ID3":
        return out
    ver = data[3]
    size = _syncsafe(data[6:10])
    tag = data[10:10 + size]
    pos = 0
    while pos + 10 <= len(tag):
        fid = tag[pos:pos + 4]
        if not fid.strip(b"\x00"):
            break
        fsize = _syncsafe(tag[pos + 4:pos + 8]) if ver >= 4 else int.from_bytes(tag[pos + 4:pos + 8], "big")
        body = tag[pos + 10:pos + 10 + fsize]
        pos += 10 + fsize
        if fid in TEXT_FRAMES and body:
            out.setdefault(TEXT_FRAMES[fid], _decode_text(body[0], body[1:]))
        elif fid == b"USLT" and len(body) > 4:
            enc = body[0]
            _lang = body[1:4]
            _desc, i = _cstr(body, enc, 4)
            out.setdefault("lyrics", _decode_text(enc, body[i:]))
        elif fid == b"APIC" and len(body) > 4:
            enc = body[0]
            j = body.find(b"\x00", 1)
            if j < 0:
                continue
            mime = body[1:j].decode("ascii", "replace")
            ptype = body[j + 1]
            _desc, i = _cstr(body, enc, j + 2)
            if ptype in (3, 0):  # front cover / other
                out.setdefault("cover", body[i:])
                out.setdefault("cover_mime", mime or "image/jpeg")
    return out


def _parse_flac(data: bytes) -> dict:
    out: dict = {}
    extra: dict[str, list[str]] = {}
    pos = 4
    while pos + 4 <= len(data):
        hdr = data[pos]
        last = hdr & 0x80
        btype = hdr & 0x7F
        length = int.from_bytes(data[pos + 1:pos + 4], "big")
        body = data[pos + 4:pos + 4 + length]
        pos += 4 + length
        if btype == 4:  # VorbisComment
            try:
                vlen = int.from_bytes(body[0:4], "little")
                p = 4 + vlen
                count = int.from_bytes(body[p:p + 4], "little")
                p += 4
                for _ in range(count):
                    ln = int.from_bytes(body[p:p + 4], "little")
                    p += 4
                    item = body[p:p + ln].decode("utf-8", "replace")
                    p += ln
                    if "=" not in item:
                        continue
                    key, value = item.split("=", 1)
                    mapped = FLAC_KEYS.get(key.upper())
                    if mapped:
                        out.setdefault(mapped, value)
                    else:
                        extra.setdefault(key.upper(), []).append(value)
            except Exception:
                pass
        elif btype == 6:  # PICTURE
            try:
                p = 0
                ptype = int.from_bytes(body[p:p + 4], "big")
                p += 4
                mlen = int.from_bytes(body[p:p + 4], "big")
                p += 4
                mime = body[p:p + mlen].decode("ascii", "replace")
                p += mlen
                dlen = int.from_bytes(body[p:p + 4], "big")
                p += 4 + dlen
                p += 16  # w/h/depth/colors
                clen = int.from_bytes(body[p:p + 4], "big")
                p += 4
                if ptype in (3, 0):
                    out.setdefault("cover", body[p:p + clen])
                    out.setdefault("cover_mime", mime or "image/jpeg")
            except Exception:
                pass
        if last:
            break
    if extra:
        out["extra"] = extra
    return out


def read_tags_bytes(data: bytes) -> dict:
    if data[:3] == b"ID3":
        return _parse_id3(data)
    if data[:4] == b"fLaC":
        return _parse_flac(data)
    return {}


def read_tags(path: str | Path, limit: int = 4_000_000) -> dict:
    p = Path(path)
    try:
        size = p.stat().st_size
        with open(p, "rb") as fh:
            head = fh.read(min(size, limit))
        return read_tags_bytes(head)
    except OSError:
        pass
    return {}


def read_lrc(path: str | Path) -> str:
    """Read a sibling .lrc. NetEase's new format mixes JSON header lines with [mm:ss] lines."""
    p = Path(path)
    if not p.exists():
        return ""
    text = ""
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = p.read_text(encoding=enc)
            break
        except (UnicodeDecodeError, OSError):
            continue
    if not text:
        return ""
    kept = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("{"):
            continue
        if s.startswith("[") and s[1:3].isalpha():  # [ti:] [ar:] [al:] [by:] [offset:]
            continue
        kept.append(s)
    return "\n".join(kept)


def sibling_lrc(media_path: str | Path) -> str:
    p = Path(media_path)
    lrc = p.with_suffix(".lrc")
    return read_lrc(lrc) if lrc.exists() else ""
