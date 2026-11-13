"""Criptografia dos tokens do Instagram guardados no banco."""
from cryptography.fernet import Fernet, InvalidToken


def generate_key() -> str:
    return Fernet.generate_key().decode()


class TokenBox:
    def __init__(self, key: str):
        if not key:
            raise RuntimeError("ENCRYPTION_KEY não definida. Gere uma com: python -m app.keys")
        self._f = Fernet(key.encode())

    def encrypt(self, plain: str) -> str:
        return self._f.encrypt(plain.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._f.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise RuntimeError("Não foi possível descriptografar o token (ENCRYPTION_KEY mudou?)") from exc
