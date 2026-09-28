"""Sensmos — parowanie HA z kontem: kod na ekranie → apka → token w zapieczętowanej paczce.

Ten sam protokół co klient Store na komputerze (PC/lib/pairing.dart, SHARED store_crypto.dart):
serwer widzi tylko skrót kodu i nasz klucz publiczny, a kod wchodzi do wyprowadzenia klucza —
paczki nie ma czym otworzyć ani podmienić.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
from typing import Any

import aiohttp
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# Bez znaków, które ludzie mylą przy przepisywaniu: zero i O, jedynka oraz I i L.
ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
CODE_LEN = 12
LABEL = b"sensmos:pair:v1"
POLL_S = 2
TIMEOUT_S = 180
_NONCE = 12
_TAG = 16


class PairingError(Exception):
    """Serwer odmówił albo paczka się nie otworzyła."""


def _raw(pub: X25519PublicKey) -> bytes:
    return pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def open_sealed(blob: str, key: X25519PrivateKey, extra: bytes) -> bytes:
    """Odwrotność StoreCrypto.sealTo: base64([ephPub 32][nonce 12][szyfrogram][tag 16])."""
    b = base64.b64decode(blob)
    if len(b) < 32 + _NONCE + _TAG:
        raise PairingError("sealed payload too short")
    eph = b[:32]
    shared = key.exchange(X25519PublicKey.from_public_bytes(eph))
    aes = HKDF(
        algorithm=SHA256(),
        length=32,
        salt=bytes(32),
        info=LABEL + eph + _raw(key.public_key()) + extra,
    ).derive(shared)
    return AESGCM(aes).decrypt(b[32 : 32 + _NONCE], b[32 + _NONCE :], None)


class Pairing:
    """Jedno parowanie: kod + jednorazowa para kluczy, żyje tyle, ile zgłoszenie na serwerze."""

    def __init__(self, be: str) -> None:
        self.be = be.rstrip("/")
        self.code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))
        self._key = X25519PrivateKey.generate()

    @property
    def pretty(self) -> str:
        return "-".join(self.code[i : i + 4] for i in range(0, CODE_LEN, 4))

    @property
    def rendezvous(self) -> str:
        return hashlib.sha256(f"sensmos:pair:{self.code}".encode()).hexdigest()[:32]

    async def offer(self, session: aiohttp.ClientSession, name: str, want: list[str]) -> None:
        try:
            async with session.post(
                f"{self.be}/v1/pair/offer",
                json={
                    "rendezvous": self.rendezvous,
                    "name": name,
                    "pub": _raw(self._key.public_key()).hex(),
                    "want": want,
                },
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                body = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise PairingError(f"connection: {err}") from err
        if not isinstance(body, dict) or body.get("ok") is not True:
            raise PairingError((body or {}).get("error") or "offer refused")

    async def wait(self, session: aiohttp.ClientSession) -> dict[str, Any]:
        """Czeka, aż apka odłoży paczkę; zwraca jej treść ({be, owner, token, scopes, …})."""
        loop = asyncio.get_running_loop()
        end = loop.time() + TIMEOUT_S
        while loop.time() < end:
            await asyncio.sleep(POLL_S)
            try:
                async with session.get(
                    f"{self.be}/v1/pair/claim/{self.rendezvous}",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    m = await resp.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError, ValueError):
                continue   # chwilowy brak sieci nie przerywa czekania
            if m.get("waiting") is True:
                continue
            if m.get("ok") is not True:
                raise PairingError(m.get("error") or "pairing expired")
            try:
                out = json.loads(open_sealed(m["blob"], self._key, self.code.encode()))
            except Exception as err:   # zły kod/klucz = InvalidTag; śmieci = ValueError
                raise PairingError("sealed payload does not open") from err
            if not str(out.get("token", "")).startswith("smt_") or not out.get("owner"):
                raise PairingError("payload without token")
            return out
        raise TimeoutError
