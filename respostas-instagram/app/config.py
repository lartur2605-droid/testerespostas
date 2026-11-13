"""Configuração lida das variáveis de ambiente (veja .env.example)."""
import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


class Config:
    # Segurança do painel
    SECRET_KEY = os.environ.get("SECRET_KEY", "")
    ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY", "")  # Fernet, protege os tokens do Instagram
    SESSION_COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "1") == "1"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    # Meta / Instagram
    META_APP_SECRET = os.environ.get("META_APP_SECRET", "")
    META_VERIFY_TOKEN = os.environ.get("META_VERIFY_TOKEN", "")
    META_GRAPH_HOST = os.environ.get("META_GRAPH_HOST", "https://graph.instagram.com").rstrip("/")
    META_GRAPH_VERSION = os.environ.get("META_GRAPH_VERSION", "v21.0")

    # Atrás de proxy HTTPS da hospedagem (Railway, Render, nginx...): confia em X-Forwarded-*
    PROXY_FIX = os.environ.get("PROXY_FIX", "1") == "1"
    TIMEZONE = os.environ.get("TIMEZONE", "America/Sao_Paulo")

    # Banco
    DATABASE_PATH = os.environ.get("DATABASE_PATH", "data/respostas.db")

    # Primeiro administrador (criado na primeira execução, se ainda não existir nenhum usuário)
    ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

    # Comportamento das respostas
    REPLY_DELAY_MIN = _int("REPLY_DELAY_MIN", 20)  # segundos, para não parecer robô
    REPLY_DELAY_MAX = _int("REPLY_DELAY_MAX", 90)
    DEFAULT_MAX_REPLIES_PER_HOUR = _int("DEFAULT_MAX_REPLIES_PER_HOUR", 30)

    # LGPD: comentários (texto e @ de quem comentou) são apagados após este prazo. 0 = nunca apagar.
    RETENTION_DAYS = _int("RETENTION_DAYS", 365)

    # IA opcional (só escolhe entre respostas já aprovadas; nunca escreve texto livre)
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-5-5")
