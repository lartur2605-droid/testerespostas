"""Comunicação com a API do Instagram (Meta). Nenhum token vai para logs ou mensagens de erro."""
import hashlib
import hmac

import requests


class MetaError(Exception):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


def verify_signature(app_secret: str, raw_body: bytes, header: str | None) -> bool:
    """Confere o cabeçalho X-Hub-Signature-256 enviado pela Meta."""
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256="):])


def extract_comments(payload: dict) -> list[dict]:
    """Aceita os dois formatos de webhook documentados (changes[] ou campo direto no entry)."""
    events = []
    if not isinstance(payload, dict) or payload.get("object") != "instagram":
        return events
    for entry in payload.get("entry", []) or []:
        account_id = str(entry.get("id", ""))
        items = []
        for change in entry.get("changes", []) or []:
            if change.get("field") == "comments":
                items.append(change.get("value") or {})
        if entry.get("field") == "comments":
            items.append(entry.get("value") or {})
        for v in items:
            comment_id = v.get("id") or v.get("comment_id")
            if not comment_id:
                continue
            sender = v.get("from") or {}
            media = v.get("media") or {}
            events.append({
                "ig_account_id": account_id,
                "comment_id": str(comment_id),
                "text": v.get("text") or "",
                "from_id": str(sender.get("id", "")),
                "from_username": sender.get("username", "") or "",
                "media_id": str(media.get("id", "")),
                "parent_id": str(v["parent_id"]) if v.get("parent_id") else None,
            })
    return events


def _error_from(resp: requests.Response) -> MetaError:
    try:
        err = resp.json().get("error", {})
        return MetaError(err.get("message", f"HTTP {resp.status_code}"), err.get("code"))
    except ValueError:
        return MetaError(f"HTTP {resp.status_code}")


def reply_to_comment(host: str, version: str, comment_id: str, message: str, token: str) -> str:
    """Publica uma resposta pública a um comentário. Devolve o ID da resposta criada."""
    url = f"{host}/{version}/{comment_id}/replies"
    try:
        resp = requests.post(url, data={"message": message, "access_token": token}, timeout=15)
    except requests.RequestException as exc:
        raise MetaError(f"Falha de rede ao falar com o Instagram ({type(exc).__name__})") from None
    if resp.status_code >= 400:
        raise _error_from(resp)
    return str(resp.json().get("id", ""))


def refresh_token(host: str, token: str) -> str:
    """Renova um token de longa duração (válido por 60 dias). Devolve o novo token."""
    try:
        resp = requests.get(
            f"{host}/refresh_access_token",
            params={"grant_type": "ig_refresh_token", "access_token": token},
            timeout=15,
        )
    except requests.RequestException as exc:
        raise MetaError(f"Falha de rede ao renovar token ({type(exc).__name__})") from None
    if resp.status_code >= 400:
        raise _error_from(resp)
    new = resp.json().get("access_token")
    if not new:
        raise MetaError("Resposta sem access_token ao renovar")
    return new
