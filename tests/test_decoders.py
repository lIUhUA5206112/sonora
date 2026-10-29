"""M2 解密器单元测试（自包含、纯标准库）。

    python tests\\test_decoders.py

做法：用**编码器逆运算**造样本，再交给被测模块解密，断言 round-trip。
QMCv2 的样本是「按文档自己造」的（EncV2 + TC-TEA + 尾部 key），
不是真实客户端下载的文件 —— 文件内会明确标注「构造样本 / 未真实样本验证」。
掩码与密钥派生另有独立参考实现（逐字节照文档/TS 源码直译）做交叉比对。
"""

from __future__ import annotations

import base64
import math
import os
import random
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import kgm as KG      # noqa: E402
from app import qmc as QMC     # noqa: E402
from app import tme as TME     # noqa: E402

M32 = 0xFFFFFFFF
RNG = random.Random(20261029)
CASES: list = []


def case(fn):
    CASES.append(fn)
    return fn


def note(text: str) -> None:
    print(f"      {text}")


# ===========================================================================
# 独立参考实现（逐字节按文档 / TS 源码直译，绝不调用被测代码的掩码）
# ===========================================================================

def ref_qmc1_mask(offset: int) -> int:
    if offset > 0x7FFF:
        offset %= 0x7FFF
    return QMC.STATIC_BOX[(offset * offset + 27) & 0xFF]


def ref_rotate(value: int, bits: int) -> int:
    r = (bits + 4) % 8
    return ((value << r) | (value >> r)) & 0xFF


def ref_map_mask(key: bytes, offset: int) -> int:
    if offset > 0x7FFF:
        offset %= 0x7FFF
    idx = (offset * offset + 71214) % len(key)
    return ref_rotate(key[idx], idx & 0x7)


class RefRC4:
    """解锁音乐 QmcRC4Cipher.decrypt 的逐行直译（含分段与 skipLen）。"""

    FIRST = 0x80
    SEG = 5120

    def __init__(self, key: bytes) -> None:
        self.key = key
        self.n = len(key)
        s = [i & 0xFF for i in range(self.n)]     # Uint8Array 语义：n > 256 时并非置换
        j = 0
        for i in range(self.n):
            j = (s[i] + j + self.key[i % self.n]) % self.n
            s[i], s[j] = s[j], s[i]
        self.s0 = s
        self.hash = 1
        for v in key:
            if not v:
                continue
            nh = (self.hash * v) & M32
            if nh == 0 or nh <= self.hash:
                break
            self.hash = nh

    def segment_key(self, sid: int) -> int:
        seed = self.key[sid % self.n]
        return int(math.floor((self.hash / ((sid + 1) * seed)) * 100.0)) % self.n

    def _first(self, data: bytearray, at: int, ln: int, offset: int) -> None:
        for i in range(ln):
            data[at + i] ^= self.key[self.segment_key(offset + i)]

    def _seg(self, data: bytearray, at: int, ln: int, offset: int) -> None:
        s = list(self.s0)
        skip = (offset % self.SEG) + self.segment_key(offset // self.SEG)
        j = k = 0
        for i in range(-skip, ln):
            j = (j + 1) % self.n
            k = (s[j] + k) % self.n
            s[j], s[k] = s[k], s[j]
            if i >= 0:
                data[at + i] ^= s[(s[j] + s[k]) % self.n]

    def decrypt(self, data: bytearray, offset: int = 0) -> None:
        to_process = len(data)
        processed = 0
        if offset < self.FIRST:
            ln = min(len(data), self.FIRST - offset)
            self._first(data, 0, ln, offset)
            to_process -= ln
            processed += ln
            offset += ln
            if to_process == 0:
                return
        if offset % self.SEG != 0:
            ln = min(self.SEG - offset % self.SEG, to_process)
            self._seg(data, processed, ln, offset)
            to_process -= ln
            processed += ln
            offset += ln
            if to_process == 0:
                return
        while to_process > self.SEG:
            self._seg(data, processed, self.SEG, offset)
            to_process -= self.SEG
            processed += self.SEG
            offset += self.SEG
        if to_process > 0:
            self._seg(data, processed, to_process, offset)


def ref_mask_v1(offset: int) -> int:
    value = 0
    while offset >= 0x11:
        value ^= KG.TABLE1[offset % KG.TABLE_SIZE]
        offset >>= 4
        value ^= KG.TABLE2[offset % KG.TABLE_SIZE]
        offset >>= 4
    return value


def ref_kgm_byte(enc: int, filekey: bytes, offset: int, vpr: bool = False) -> int:
    y = (KG.TABLE_V2[offset % KG.TABLE_SIZE] ^ ref_mask_v1(offset >> 4)) ^ enc ^ filekey[offset % 17]
    y ^= (y & 0x0F) << 4
    if vpr:
        y ^= KG.VPR_KEY[offset % 17]
    return y


def ref_kgm_enc_byte(plain: int, filekey: bytes, offset: int, vpr: bool = False) -> int:
    """解密器的逆运算（T() 是非线性收尾，不能直接把解密函数当加密用）。"""
    y = plain ^ (KG.VPR_KEY[offset % 17] if vpr else 0)
    y ^= (y & 0x0F) << 4
    return (KG.TABLE_V2[offset % KG.TABLE_SIZE] ^ ref_mask_v1(offset >> 4)) ^ y ^ filekey[offset % 17]


# --- TC-TEA 编码器（解码器的逆运算，用来造 QMCv2 样本） ---------------------

def tea_encrypt_block(block: bytes, key: bytes, rounds: int = 32) -> bytes:
    k = [int.from_bytes(key[i:i + 4], "big") for i in (0, 4, 8, 12)]
    v0 = int.from_bytes(block[0:4], "big")
    v1 = int.from_bytes(block[4:8], "big")
    total = 0
    for _ in range(rounds // 2):
        total = (total + QMC.TEA_DELTA) & M32
        mix = ((((v1 << 4) + k[0]) & M32) ^ ((v1 + total) & M32) ^ (((v1 >> 5) + k[1]) & M32))
        v0 = (v0 + mix) & M32
        mix = ((((v0 << 4) + k[2]) & M32) ^ ((v0 + total) & M32) ^ (((v0 >> 5) + k[3]) & M32))
        v1 = (v1 + mix) & M32
    return v0.to_bytes(4, "big") + v1.to_bytes(4, "big")


def _xor8(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


def enc_tencent_tea(body: bytes, key: bytes) -> bytes:
    """PadLen(1)+Padding+Salt(2)+Body+Zero(7) → TC-TEA 密文（解码器的逆运算）。"""
    pad = (-(10 + len(body))) % 8
    stream = bytes([pad]) + bytes(pad) + b"\x11\x22" + body + bytes(7)
    assert len(stream) % 8 == 0, len(stream)
    blocks = [stream[i:i + 8] for i in range(0, len(stream), 8)]
    out = bytearray(tea_encrypt_block(blocks[0], key))
    a_prev, c_prev = blocks[0], bytes(out)
    for m in range(1, len(blocks)):
        a_m = _xor8(blocks[m], c_prev)
        c_m = _xor8(a_prev, tea_encrypt_block(a_m, key))
        out += c_m
        a_prev, c_prev = a_m, c_m
    return bytes(out)


def build_ekey(derived: bytes) -> bytes:
    """派生密钥 → 文件里的 ekey 原文（Base64 文本）。"""
    first, body = derived[:8], derived[8:]
    tea_key = bytearray(16)
    simple = QMC.simple_make_key(106, 8)
    for i in range(8):
        tea_key[i * 2] = simple[i]
        tea_key[i * 2 + 1] = first[i]
    raw = first + enc_tencent_tea(body, bytes(tea_key))
    return base64.b64encode(raw)


def build_ekey_encv2(derived: bytes) -> bytes:
    """同上，但再套一层 QQMusic EncV2,Key:（mixKey1/mixKey2 双层 TC-TEA）。"""
    inner = build_ekey(derived)          # 内层是 Base64 文本
    wrapped = enc_tencent_tea(inner, QMC.MIX_KEY2)
    wrapped = enc_tencent_tea(wrapped, QMC.MIX_KEY1)
    return base64.b64encode(QMC.ENC_V2_PREFIX + wrapped)


# ===========================================================================
# 样本构造
# ===========================================================================

def plain_mp3(size: int = 4096) -> bytes:
    return b"ID3\x04\x00\x00\x00\x00\x00\x00" + bytes(RNG.randrange(256) for _ in range(size))


def plain_flac(size: int = 4096) -> bytes:
    return b"fLaC" + bytes(RNG.randrange(256) for _ in range(size))


def plain_mp4(size: int = 4096) -> bytes:
    return (b"\x00\x00\x00\x20ftypM4A \x00\x00\x00\x00"
            + bytes(RNG.randrange(256) for _ in range(size)))


def enc_qmc1(plain: bytes) -> bytes:
    return bytes(p ^ ref_qmc1_mask(i) for i, p in enumerate(plain))


def enc_map(plain: bytes, key: bytes) -> bytes:
    return bytes(p ^ ref_map_mask(key, i) for i, p in enumerate(plain))


def enc_rc4(plain: bytes, key: bytes) -> bytes:
    buf = bytearray(plain)
    RefRC4(key).decrypt(buf, 0)
    return bytes(buf)


def qmc2_plain_tail(cipher: bytes, ekey: bytes) -> bytes:
    return cipher + ekey + len(ekey).to_bytes(4, "little")


def qmc2_qtag_tail(cipher: bytes, ekey: bytes, song_id: int = 987654321) -> bytes:
    raw_key = ekey + b"," + str(song_id).encode()
    return cipher + raw_key + len(raw_key).to_bytes(4, "big") + b"QTag"


def build_kgm(plain: bytes, filekey: bytes, vpr: bool = False, header_len: int = 0x3C) -> bytes:
    header = bytearray(max(header_len, 0x3C))
    header[0:16] = KG.VPR_HEADER if vpr else KG.KGM_HEADER
    header[0x10:0x14] = header_len.to_bytes(4, "little")
    header[0x1C:0x2C] = filekey
    fk = filekey + b"\x00"
    body = bytes(ref_kgm_enc_byte(p, fk, i, vpr) for i, p in enumerate(plain))
    return bytes(header) + body


class TmpDir:
    def __enter__(self):
        self._d = tempfile.TemporaryDirectory(prefix="sonora-dec-")
        return Path(self._d.name)

    def __exit__(self, *exc):
        self._d.cleanup()


def write(dirpath: Path, name: str, data: bytes) -> str:
    p = dirpath / name
    p.write_bytes(data)
    return str(p)


def check_result(res: dict, fmt: str, audio: bytes) -> None:
    assert isinstance(res, dict), type(res)
    assert set(res) == {"audio", "format", "meta", "cover", "cover_fmt"}, set(res)
    assert res["format"] == fmt, f"format={res['format']} want {fmt}"
    assert res["audio"] == audio, f"audio mismatch: {len(res['audio'])} vs {len(audio)}"
    assert res["meta"] == {} and res["cover"] == b"" and res["cover_fmt"] == "", res


def collect_progress():
    seen: list[float] = []

    def cb(v: float) -> None:
        assert 0.0 <= v <= 1.0000001, v
        seen.append(v)

    return seen, cb


# ===========================================================================
# QMCv1
# ===========================================================================

@case
def qmc1_mask_matches_reference():
    """静态表掩码 vs 文档公式（重点覆盖 0x7FFF / 0x8000 回绕边界）。"""
    cipher = QMC._StaticCipher()
    ranges = [(0, 256), (0x7F00, 0x300), (0x7FF0, 0x40), (0x8000, 0x300),
              (0xFFF0, 0x40), (0x10000, 0x300), (0x1FFFF, 0x40), (0x25000, 0x200)]
    checked = 0
    for start, length in ranges:
        got = cipher.masks(start, length)
        want = bytes(ref_qmc1_mask(o) for o in range(start, start + length))
        assert got == want, f"掩码不一致 @{start:#x}+{length}"
        checked += length
    note(f"8 段、共 {checked} 字节掩码与文档公式逐字节一致（含 0x7FFF 边界）")


@case
def qmc1_roundtrip_mp3():
    plain = plain_mp3()
    with TmpDir() as d:
        path = write(d, "a.qmc0", enc_qmc1(plain))
        seen, cb = collect_progress()
        res = QMC.decrypt(path, cb)
    check_result(res, "mp3", plain)
    assert seen and seen[-1] == 1.0 and all(b >= a for a, b in zip(seen, seen[1:])), seen
    note(f"qmc0 → mp3 {len(plain)}B，进度 {len(seen)} 次，末值 {seen[-1]}")


@case
def qmc1_roundtrip_flac_large():
    plain = plain_flac(1_500_000)   # > 0x7FFF*… 跨多个分块，覆盖 >1MiB 的流式路径
    with TmpDir() as d:
        path = write(d, "a.qmcflac", enc_qmc1(plain))
        res = QMC.decrypt(path)
    check_result(res, "flac", plain)
    note(f"qmcflac → flac {len(plain)}B（跨 2 个 1MiB 分块）")


# ===========================================================================
# QMCv2（构造样本，非真实客户端样本）
# ===========================================================================

@case
def map_cipher_matches_reference():
    key = bytes(RNG.randrange(1, 256) for _ in range(257))
    cipher = QMC._MapCipher(key)
    got = cipher.masks(0x8000 - 40, 400)      # 跨回绕边界
    want = bytes(ref_map_mask(key, o) for o in range(0x8000 - 40, 0x8000 - 40 + 400))
    assert got == want
    note("Map 密码掩码跨 0x7FFF 边界与文档公式逐字节一致")


@case
def rc4_cipher_matches_reference():
    key = bytes(RNG.randrange(1, 256) for _ in range(320))
    cipher = QMC._RC4Cipher(key)
    for start, length in ((0, 64), (0, 128), (0, 300), (0, 5000), (128, 5000),
                          (5000, 100), (5120, 100), (10240, 7200)):
        zeros = bytearray(length)
        RefRC4(key).decrypt(zeros, start)     # 0 ^ mask => mask
        got = cipher.masks(start, length)
        assert got == bytes(zeros), f"RC4 掩码不一致 @{start}+{length}"
    note("分段 RC4 掩码在 8 组 (offset, len) 上与 TS 直译参考实现一致")


@case
def tencent_tea_roundtrip():
    key = bytes(RNG.randrange(256) for _ in range(16))
    for size in (1, 7, 8, 9, 17, 64, 249, 505):
        body = bytes(RNG.randrange(256) for _ in range(size))
        enc = enc_tencent_tea(body, key)
        assert len(enc) % 8 == 0
        out = QMC._tencent_tea_decrypt(enc, key)
        assert out == body, f"TC-TEA round-trip 失败：{size}"
    note("TC-TEA 8 组长度 round-trip 通过（含末尾 7 字节 Zero 校验）")


@case
def derive_key_encv2_roundtrip():
    for length in (257, 513):
        derived = bytes(RNG.randrange(256) for _ in range(length))
        assert QMC.derive_key(build_ekey(derived)) == derived
        assert QMC.derive_key(build_ekey_encv2(derived)) == derived
    note("derive_key：普通 ekey 与 EncV2 双层（mixKey1+mixKey2）均能还原派生密钥")


@case
def qmc2_map_file_plain_tail():
    derived = bytes(RNG.randrange(256) for _ in range(257))
    plain = plain_mp3(3000)
    cipher = enc_map(plain, derived)
    ekey = build_ekey(derived)
    with TmpDir() as d:
        path = write(d, "b.mflac", qmc2_plain_tail(cipher, ekey))
        res = QMC.decrypt(path)
        found, audio_size, song = QMC.find_cipher(Path(path).read_bytes())
    assert audio_size == len(cipher), (audio_size, len(cipher))
    assert song is None
    check_result(res, "mp3", plain)
    note(f"小端尾部 key（{len(ekey)}B）→ Map 密码 → 还原 {len(plain)}B mp3")

    # 再走一遍 EncV2 包装
    ekey2 = build_ekey_encv2(derived)
    with TmpDir() as d:
        path = write(d, "b2.mgg", qmc2_plain_tail(cipher, ekey2))
        res = QMC.decrypt(path)
    check_result(res, "mp3", plain)
    note(f"EncV2 包装（{len(ekey2)}B）同样还原成功")


@case
def qmc2_rc4_file_qtag():
    derived = bytes(RNG.randrange(1, 256) for _ in range(513))   # > 300 → 分段 RC4
    plain = plain_flac(9000)
    cipher = enc_rc4(plain, derived)
    ekey = build_ekey(derived)
    with TmpDir() as d:
        path = write(d, "c.mgg", qmc2_qtag_tail(cipher, ekey, 1234567))
        res = QMC.decrypt(path)
        _cipher, audio_size, song = QMC.find_cipher(Path(path).read_bytes())
    assert audio_size == len(cipher)
    assert song == 1234567, song
    check_result(res, "flac", plain)
    note(f"QTag 大端尾（keySize={len(ekey) + 1 + 7}）→ 分段 RC4 → 还原 9000B flac，songID={song}")


@case
def qmc2_key_size_falls_back_to_v1():
    plain = plain_mp3(2000)
    cipher = bytearray(enc_qmc1(plain))
    cipher[-4:] = (0x400).to_bytes(4, "little")     # 尾部声明 key_size >= 0x400
    expect = bytes(c ^ ref_qmc1_mask(i) for i, c in enumerate(cipher))
    with TmpDir() as d:
        path = write(d, "d.qmc3", bytes(cipher))
        res = QMC.decrypt(path)
    check_result(res, "mp3", expect)
    note("尾部 key_size >= 0x400 → 回落 QMCv1 静态表（整文件，不剥尾部）")


@case
def qmc2_stag_raises():
    data = plain_flac(500)[:-4] + b"STag"
    with TmpDir() as d:
        path = write(d, "e.mflac", data)
        try:
            QMC.decrypt(path)
        except ValueError as exc:
            assert "密钥缺失" in str(exc), exc
            note(f"STag → {exc}")
            return
    raise AssertionError("STag 未报错")


@case
def mflach_raises():
    with TmpDir() as d:
        path = write(d, "f.mflach", b"\x00" * 64)
        try:
            QMC.decrypt(path)
        except ValueError as exc:
            assert str(exc) == "mflach 暂不支持", exc
            note("mflach → ValueError('mflach 暂不支持')")
            return
    raise AssertionError("mflach 未报错")


# ===========================================================================
# TME
# ===========================================================================

@case
def tme_tm0_tm3_passthrough():
    mp3 = plain_mp3(1000)
    mp4 = plain_mp4(1000)
    with TmpDir() as d:
        res0 = TME.decrypt(write(d, "a.tm0", mp3))
        res3 = TME.decrypt(write(d, "a.tm3", mp3))
        res_m4a = TME.decrypt(write(d, "b.tm0", mp4))
    check_result(res0, "mp3", mp3)
    check_result(res3, "mp3", mp3)
    check_result(res_m4a, "m4a", mp4)
    note("tm0/tm3 内容未加密：按魔数识别（ID3→mp3，ftyp→m4a）")


@case
def tme_tm2_tm6_header_patch():
    mp4 = plain_mp4(5000)
    broken = b"\x00" * 8 + mp4[8:]          # 客户端把前 8 字节抹成 0
    with TmpDir() as d:
        res2 = TME.decrypt(write(d, "a.tm2", broken))
        res6 = TME.decrypt(write(d, "a.tm6", broken))
    assert res2["audio"][:8] == TME.TM_HEADER, res2["audio"][:8]
    check_result(res2, "m4a", mp4)
    check_result(res6, "m4a", mp4)
    note("tm2/tm6：回填 00 00 00 20 66 74 79 70 → 与原始 mp4 完全一致")


@case
def tme_tkm_uses_qmc1():
    mp4 = plain_mp4(3000)
    enc = enc_qmc1(mp4)
    with TmpDir() as d:
        res = TME.decrypt(write(d, "a.tkm", enc))
    check_result(res, "m4a", mp4)
    note("tkm：QMCv1 静态表通道 → m4a")


@case
def tme_unknown_ext_raises():
    with TmpDir() as d:
        try:
            TME.decrypt(write(d, "a.tm1", b"\x00" * 32))
        except ValueError as exc:
            assert "不是 TME 格式" in str(exc), exc
            note(f"未知后缀 → {exc}")
            return
    raise AssertionError("未知后缀未报错")


# ===========================================================================
# 酷狗 KGM / KGMA / VPR
# ===========================================================================

@case
def kgm_keystream_matches_reference():
    """分组加速的 keystream 与上游逐字节公式完全等价。"""
    fk = bytes(RNG.randrange(256) for _ in range(16)) + b"\x00"
    data = bytes(RNG.randrange(256) for _ in range(70000))
    for vpr in (False, True):
        want = bytes(ref_kgm_byte(c, fk, i, vpr) for i, c in enumerate(data))
        got = KG._decrypt_body(data, fk, vpr)
        assert got == want, f"KGM keystream 不一致（vpr={vpr}）"
    # 非 16 对齐的起始偏移也要一致
    start = 100_003
    piece = bytes(RNG.randrange(256) for _ in range(5000))
    got = KG._keystream(start, len(piece))
    want = bytes(KG.TABLE_V2[(start + i) % 272] ^ ref_mask_v1((start + i) >> 4) for i in range(len(piece)))
    assert got == want, "keystream 非对齐起始不一致"
    note("KGM keystream：70KB 全流（kgm/vpr）+ 非 16 对齐起始 5000B 与上游公式一致")


@case
def kgm_roundtrip():
    filekey = bytes(RNG.randrange(256) for _ in range(16))
    plain = plain_flac(12000)
    for ext in (".kgm", ".kgma"):
        with TmpDir() as d:
            path = write(d, "a" + ext, build_kgm(plain, filekey))
            seen, cb = collect_progress()
            res = KG.decrypt(path, cb)
        check_result(res, "flac", plain)
        assert seen[-1] == 1.0
    # 头部大于 0x3C（0x50）时也要正确跳过头
    with TmpDir() as d:
        path = write(d, "b.kgm", build_kgm(plain, filekey, header_len=0x50))
        check_result(KG.decrypt(path), "flac", plain)
    note("kgm/kgma round-trip 通过（header_len=0x3C 与 0x50 两种）")


@case
def kgm_mp3_roundtrip():
    filekey = bytes(RNG.randrange(256) for _ in range(16))
    plain = plain_mp3(8000)
    with TmpDir() as d:
        res = KG.decrypt(write(d, "a.kgma", build_kgm(plain, filekey)))
    check_result(res, "mp3", plain)
    note("kgma（mp3 内容）→ mp3")


@case
def vpr_roundtrip():
    filekey = bytes(RNG.randrange(256) for _ in range(16))
    plain = plain_flac(5000)
    with TmpDir() as d:
        res = KG.decrypt(write(d, "a.vpr", build_kgm(plain, filekey, vpr=True)))
    check_result(res, "flac", plain)
    note("vpr（额外 vprKey 异或）round-trip 通过")


@case
def kgm_errors():
    with TmpDir() as d:
        for name, data, want in (
            ("x.kgm", b"short", "文件头过短"),
            ("y.kgm", b"\x00" * 200, "文件头不匹配"),
        ):
            try:
                KG.decrypt(write(d, name, data))
            except ValueError as exc:
                assert want in str(exc), exc
            else:
                raise AssertionError(f"{name} 未报错")
        # 头部长度非法
        head = bytearray(0x3C)
        head[0:16] = KG.KGM_HEADER
        head[0x10:0x14] = (10).to_bytes(4, "little")
        try:
            KG.decrypt(write(d, "z.kgm", bytes(head) + b"\x00" * 100))
        except ValueError as exc:
            assert "音频偏移非法" in str(exc), exc
        else:
            raise AssertionError("坏 header_len 未报错")
        # kgg 明确不支持
        try:
            KG.decrypt(write(d, "k.kgg", b"\x00" * 100))
        except ValueError as exc:
            assert "kgg" in str(exc), exc
        else:
            raise AssertionError("kgg 未报错")
    note("错误路径：过短 / 魔数不符 / header_len 非法 / kgg 均给出中文提示")


# ===========================================================================
# 统一接口一致性
# ===========================================================================

@case
def interface_shape_consistent():
    key = bytes(RNG.randrange(1, 256) for _ in range(257))
    ekey = build_ekey(key)
    plain = plain_mp3(1000)
    with TmpDir() as d:
        res = QMC.decrypt(write(d, "a.qmc0", qmc2_plain_tail(enc_map(plain, key), ekey)))
        assert res["format"] == "mp3" and res["audio"] == plain
        assert res["meta"] == {} and res["cover"] == b"" and res["cover_fmt"] == ""
    note("三个模块返回值均为 {audio, format, meta:{}, cover:b'', cover_fmt:''}")


# ===========================================================================
# runner
# ===========================================================================

def main() -> int:
    print(f"python {sys.version.split()[0]}  |  root={ROOT}")
    failed = 0
    for fn in CASES:
        try:
            fn()
        except Exception as exc:                       # noqa: BLE001
            failed += 1
            print(f"FAIL  {fn.__name__}: {exc}")
            traceback.print_exc()
        else:
            print(f"PASS  {fn.__name__}")
    total = len(CASES)
    print()
    if failed:
        print(f"FAIL: {failed}/{total} 组失败")
        return 1
    print(f"PASS: {total}/{total} 组全部通过")
    print("说明：QMCv2 样本由本文件的编码器逆运算构造，非真实客户端样本；"
          "真实 mflac/mgg 端到端仍未验证（无授权样本）。")
    return 0


if __name__ == "__main__":
    if os.name == "nt":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
