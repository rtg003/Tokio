"""Keyring AES-256-GCM: round-trip, rejeição de segredo errado, tamper."""
from __future__ import annotations

import pytest

from engine.core import keyring


SECRET = "unit-test-keyring-secret-高entropia"
PLAINTEXT = "0x" + "de" * 32  # parece uma agent private key


def test_roundtrip() -> None:
    token = keyring.encrypt(PLAINTEXT, secret=SECRET)
    assert token != PLAINTEXT
    assert PLAINTEXT not in token  # cifrado, não codificado
    assert keyring.decrypt(token, secret=SECRET) == PLAINTEXT


def test_nonce_is_random() -> None:
    # Dois ciphertexts do mesmo plaintext diferem (IV aleatório por cifra).
    a = keyring.encrypt(PLAINTEXT, secret=SECRET)
    b = keyring.encrypt(PLAINTEXT, secret=SECRET)
    assert a != b
    assert keyring.decrypt(a, secret=SECRET) == keyring.decrypt(b, secret=SECRET)


def test_wrong_secret_rejected() -> None:
    token = keyring.encrypt(PLAINTEXT, secret=SECRET)
    with pytest.raises(keyring.KeyringError):
        keyring.decrypt(token, secret="segredo-errado")


def test_tampered_ciphertext_rejected() -> None:
    token = keyring.encrypt(PLAINTEXT, secret=SECRET)
    # Vira um caractere no meio do token base64 → tag GCM inválida.
    idx = len(token) // 2
    flipped = "A" if token[idx] != "A" else "B"
    tampered = token[:idx] + flipped + token[idx + 1 :]
    with pytest.raises(keyring.KeyringError):
        keyring.decrypt(tampered, secret=SECRET)


def test_missing_secret_raises() -> None:
    with pytest.raises(keyring.KeyringError):
        keyring.encrypt(PLAINTEXT, secret="")


def test_keyring_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TOKIO_KEYRING_SECRET", raising=False)
    assert keyring.keyring_configured() is False
    monkeypatch.setenv("TOKIO_KEYRING_SECRET", "x")
    assert keyring.keyring_configured() is True
