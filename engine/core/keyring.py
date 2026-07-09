"""Keyring cifrado (AES-256-GCM) para as agent private keys da Hyperliquid.

As agent keys deixam de viver em texto puro no `.env` e passam a ser cifradas
em repouso no SQLite (`hl_agents.privkey_enc`). A chave de cifra é derivada de
`TOKIO_KEYRING_SECRET` (env/systemd — NUNCA no YAML, NUNCA no backup offsite:
DISCOVERY V7). Plaintext NUNCA é logado (regra do repo — `config.py`).

Formato do token cifrado: base64url( iv(12) || AESGCM(ct+tag) ). O AAD é fixo
(`tokio-hl-agent`) para amarrar o ciphertext a este uso e recusar reuso cruzado.

Derivação da chave: SHA-256(secret) → 32 bytes. É um wrapping key simétrico
determinístico (mesmo secret ⇒ mesma chave ⇒ decifra após restart), coerente
com o segredo vindo do systemd. Não é hashing de senha de usuário — é
derivação de uma chave de cifra a partir de um segredo de alta entropia posto
pelo operador; por isso SHA-256 (e não um KDF lento) é adequado.
"""
from __future__ import annotations

import base64
import hashlib
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_AAD = b"tokio-hl-agent"
_IV_LEN = 12
_SECRET_VAR = "TOKIO_KEYRING_SECRET"


class KeyringError(RuntimeError):
    """Falha de configuração/decifra do keyring (nunca carrega plaintext)."""


def keyring_configured() -> bool:
    return bool(os.environ.get(_SECRET_VAR))


def _derive_key(secret: str | None = None) -> bytes:
    raw = secret if secret is not None else os.environ.get(_SECRET_VAR)
    if not raw:
        raise KeyringError(f"{_SECRET_VAR} ausente — keyring não configurado")
    return hashlib.sha256(raw.encode("utf-8")).digest()


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


def _b64d(token: str) -> bytes:
    return base64.urlsafe_b64decode(token.encode("ascii"))


def encrypt(plaintext: str, *, secret: str | None = None) -> str:
    """Cifra e retorna base64url(iv || ct+tag). `plaintext` é a agent key."""
    key = _derive_key(secret)
    iv = os.urandom(_IV_LEN)
    ct = AESGCM(key).encrypt(iv, plaintext.encode("utf-8"), _AAD)
    return _b64e(iv + ct)


def decrypt(token: str, *, secret: str | None = None) -> str:
    """Decifra um token produzido por `encrypt`. Erros nunca vazam plaintext."""
    key = _derive_key(secret)
    try:
        blob = _b64d(token)
    except Exception as exc:  # noqa: BLE001 — base64 corrompido
        raise KeyringError("privkey_enc malformado") from exc
    if len(blob) <= _IV_LEN:
        raise KeyringError("privkey_enc truncado")
    iv, ct = blob[:_IV_LEN], blob[_IV_LEN:]
    try:
        return AESGCM(key).decrypt(iv, ct, _AAD).decode("utf-8")
    except InvalidTag as exc:
        # Segredo errado ou ciphertext adulterado. Não revelar mais que isso.
        raise KeyringError("falha ao decifrar privkey_enc (segredo incorreto?)") from exc
