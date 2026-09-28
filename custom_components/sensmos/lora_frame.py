"""Sensmos — dekodowanie ramki DATA (0xE0 0x02), port 1:1 z FW lora_scan.cpp (data_rx_process).

Ramka: [E0][02][flagi][dst 4][sub] + (AES: [nonce 4]) + [payload][CRC32 LE 4]
- flagi: bit0 = AES, bit1 = LAST (tu ignorowany — jak ścieżka WS w FW; echo odcina BE)
- AES-256-CTR, klucz = sha256(fraza), licznik = nonce4 ‖ dst4 ‖ sub ‖ 0×7; szyfrowany payload+CRC
- CRC32 (zlib) wyłącznie z jawnego payloadu, little-endian
"""
from __future__ import annotations

import hashlib
import re
import zlib
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

F_AES = 0x01
DEDUP_S = 120      # FW: ta sama suma w oknie 120 s = powtórka
RING = 6           # FW: LORA_DATA_INBOX_SIZE
_HEX = re.compile(r"[0-9a-fA-F]*")


@dataclass
class Frame:
    sub: int
    enc: bool
    payload: bytes
    crc: int       # CRC32 payloadu — klucz powtórki
    fh: int        # CRC32 surowej ramki — do kwitu, jak w FW


def key_from_phrase(phrase: str) -> bytes:
    """FW: sha256 z surowych bajtów frazy (UTF-8, bez przycinania)."""
    return hashlib.sha256(phrase.encode("utf-8")).digest()


def _ctr(key: bytes, nonce: bytes, dst: bytes, sub: int, data: bytes) -> bytes:
    c = Cipher(algorithms.AES(key), modes.CTR(nonce + dst + bytes([sub]) + bytes(7))).decryptor()
    return c.update(data) + c.finalize()


def is_text(p: bytes) -> bool:
    """FW data_is_text: drukowalne ASCII bez \" i \\ — bezpieczne 1:1 w JSON."""
    return all(0x20 <= b <= 0x7E and b not in (0x22, 0x5C) for b in p)


def decode(hexstr: str, dst8: str, key: bytes | None, accept_open: bool) -> Frame | None:
    """Ramka do bazy `dst8` albo None (nie do nas / brak klucza / jawna bez zgody / zły CRC)."""
    if len(hexstr) % 2 or not 26 <= len(hexstr) <= 408 or not _HEX.fullmatch(hexstr):
        return None
    try:
        d = bytes.fromhex(hexstr)
        dst = bytes.fromhex(dst8[:8])
    except ValueError:
        return None
    if d[0] != 0xE0 or d[1] != 0x02 or d[3:7] != dst:
        return None
    sub, aes = d[7], bool(d[2] & F_AES)
    if not aes and not accept_open:
        return None
    if aes:
        if key is None:
            return None
        buf = d[12:]
        if not 5 <= len(buf) <= 132:
            return None
        buf = _ctr(key, d[8:12], d[3:7], sub, buf)
    else:
        buf = d[8:]
        if not 5 <= len(buf) <= 132:
            return None
    p, crc = buf[:-4], int.from_bytes(buf[-4:], "little")
    if zlib.crc32(p) != crc:
        return None
    return Frame(sub=sub, enc=aes, payload=p, crc=crc, fh=zlib.crc32(d))


def ring_item(fr: Frame, ts: int, via: str) -> dict:
    """Wpis inboxu w kształcie FW /lora/inbox: ts, sub, enc, via, text|hex."""
    item = {"ts": ts, "sub": fr.sub, "enc": fr.enc, "via": via}
    if is_text(fr.payload):
        item["text"] = fr.payload.decode("ascii")
    else:
        item["hex"] = fr.payload.hex()
    return item
