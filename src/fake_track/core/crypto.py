import base64
import os

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def _pkcs7_pad(data: bytes) -> bytes:
    padder = padding.PKCS7(128).padder()
    return padder.update(data) + padder.finalize()


def _pkcs7_unpad(data: bytes) -> bytes:
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(data) + unpadder.finalize()


def aes_encrypt(plaintext: str, key: str) -> str:
    key_bytes = key.encode("utf-8")
    iv = os.urandom(16)
    cipher = Cipher(algorithms.AES(key_bytes), modes.CBC(iv))
    encryptor = cipher.encryptor()
    padded = _pkcs7_pad(plaintext.encode("utf-8"))
    encrypted = encryptor.update(padded) + encryptor.finalize()
    return f"{base64.b64encode(iv).decode('ascii')}:{base64.b64encode(encrypted).decode('ascii')}"


def aes_decrypt(payload: str, key: str) -> str:
    iv_b64, cipher_b64 = payload.split(":", 1)
    iv = base64.b64decode(iv_b64)
    ciphertext = base64.b64decode(cipher_b64)
    cipher = Cipher(algorithms.AES(key.encode("utf-8")), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    return _pkcs7_unpad(padded).decode("utf-8")


def encryption_self_check(key: str) -> None:
    sample = '{"ping":1}'
    c1 = aes_encrypt(sample, key)
    c2 = aes_encrypt(sample, key)
    if c1 == c2:
        raise ValueError("AES encryption self-check failed: IV does not appear random")
    if aes_decrypt(c1, key) != sample:
        raise ValueError("AES encryption self-check failed: decrypt mismatch")
