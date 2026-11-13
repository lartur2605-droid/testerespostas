"""Gera valores secretos para o .env:  python -m app.keys"""
import secrets

from .crypto import generate_key

if __name__ == "__main__":
    print("SECRET_KEY=" + secrets.token_hex(32))
    print("ENCRYPTION_KEY=" + generate_key())
    print("META_VERIFY_TOKEN=" + secrets.token_urlsafe(24))
