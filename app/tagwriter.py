"""Minimal tag writers (ID3v2.4 for MP3, VorbisComment + PICTURE for FLAC).

Standard library only. Writes title / artist / album / cover / lyrics /
track / date, and can carry extra Vorbis comments through a FLAC transcode.
"""

from __future__ import annotations

import struct
from pathlib import Path

VENDOR = "sonora 0.2"


def _syncsafe(n: int) -> bytes:
    return bytes(((n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F))


def _text_frame(fid: bytes, value: str) -> bytes:
    if not value:
        return b""
    payload = b"\x03" + value.encode("utf-8")
    return fid + _syncsafe(len(payload)) + b"\x00\x00" + payload


def _usrl_frame(lyrics: str) -> bytes:
    if not lyrics:
        return b""
    payload = b"\x03" + b"chi" + b"\x00" + lyrics.encode("utf-8")
    return b"USLT" + _syncsafe(len(payload)) + b"\x00\x00" + payload


def _apic_frame(cover: bytes, mime: str) -> bytes:
    if not cover:
        return b""
    payload = b"\x03" + mime.encode("ascii") + b"\x00" + b"\x03" + b"\x00" + cover
    return b"APIC" + _syncsafe(len(payload)) + b"\x00\x00" + payload


def write_mp3(path: str | Path, title: str, artist: str, album: str,
              cover: bytes = b"", mime: str = "image/jpeg",
              lyrics: str = "", track: str = "", date: str = "") -> None:
    p = Path(path)
    data = p.read_bytes()
    body = data
    if data[:3] == b"ID3" and len(data) >= 10:
        size = ((data[6] & 0x7F) << 21) | ((data[7] & 0x7F) << 14) | ((data[8] & 0x7F) << 7) | (data[9] & 0x7F)
        body = data[10 + size:]
    if len(body) >= 128 and body[-128:-125] == b"TAG":
        body = body[:-128]
    frames = b""
    frames += _text_frame(b"TIT2", title)
    frames += _text_frame(b"TPE1", artist)
    frames += _text_frame(b"TALB", album)
    frames += _text_frame(b"TRCK", track)
    frames += _text_frame(b"TDRC", date)
    frames += _usrl_frame(lyrics)
    frames += _apic_frame(cover, mime)
    tag = b"ID3\x04\x00\x00" + _syncsafe(len(frames)) + frames
    p.write_bytes(tag + body)


def _vorbis_comment(fields: list[tuple[str, str]], extra: dict[str, list[str]] | None) -> bytes:
    items: list[bytes] = []
    seen: set[str] = set()
    for key, value in fields:
        if value:
            items.append(f"{key}={value}".encode("utf-8"))
            seen.add(key.upper())
    for key, values in (extra or {}).items():
        if key.upper() in seen:
            continue
        for value in values:
            items.append(f"{key}={value}".encode("utf-8"))
    out = struct.pack("<I", len(VENDOR)) + VENDOR.encode("ascii")
    out += struct.pack("<I", len(items))
    for item in items:
        out += struct.pack("<I", len(item)) + item
    return out


def _picture_block(cover: bytes, mime: str) -> bytes:
    out = struct.pack(">I", 3)  # front cover
    m = mime.encode("ascii")
    out += struct.pack(">I", len(m)) + m
    out += struct.pack(">I", 0)  # empty description
    out += struct.pack(">IIII", 0, 0, 0, 0)
    out += struct.pack(">I", len(cover)) + cover
    return out


def write_flac(path: str | Path, title: str, artist: str, album: str,
               cover: bytes = b"", mime: str = "image/jpeg",
               lyrics: str = "", track: str = "", date: str = "",
               extra: dict[str, list[str]] | None = None) -> None:
    p = Path(path)
    data = p.read_bytes()
    if data[:4] != b"fLaC":
        raise ValueError("目标不是 FLAC 流")

    pos = 4
    blocks: list[tuple[int, bytes]] = []
    while True:
        if pos + 4 > len(data):
            raise ValueError("FLAC 元数据块损坏")
        header = data[pos]
        last = header & 0x80
        btype = header & 0x7F
        length = int.from_bytes(data[pos + 1:pos + 4], "big")
        blocks.append((btype, data[pos + 4:pos + 4 + length]))
        pos += 4 + length
        if last:
            break
    audio = data[pos:]

    kept = [(t, b) for (t, b) in blocks if t not in (4, 6)]
    fields = [("TITLE", title), ("ARTIST", artist), ("ALBUM", album),
              ("TRACKNUMBER", track), ("DATE", date), ("LYRICS", lyrics)]
    kept.append((4, _vorbis_comment(fields, extra)))
    if cover:
        kept.append((6, _picture_block(cover, mime)))

    out = bytearray(b"fLaC")
    for i, (btype, body) in enumerate(kept):
        flag = 0x80 if i == len(kept) - 1 else 0x00
        out.append(flag | btype)
        out += len(body).to_bytes(3, "big")
        out += body
    out += audio
    p.write_bytes(bytes(out))


def write_tags(path: str | Path, target: str, title: str, artist: str, album: str,
               cover: bytes = b"", mime: str = "image/jpeg",
               lyrics: str = "", track: str = "", date: str = "",
               extra: dict[str, list[str]] | None = None) -> None:
    if target == "mp3":
        write_mp3(path, title, artist, album, cover, mime, lyrics, track, date)
    elif target == "flac":
        write_flac(path, title, artist, album, cover, mime, lyrics, track, date, extra)
    else:
        raise ValueError(f"unsupported tag target: {target}")
