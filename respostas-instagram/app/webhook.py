"""Endpoint que a Meta chama: verificação (GET) e avisos de comentários (POST)."""
import hmac
import json

from flask import Blueprint, abort, current_app, request

from . import meta

bp = Blueprint("webhook", __name__)


@bp.get("/webhook")
def verify():
    cfg = current_app.config
    token = cfg["META_VERIFY_TOKEN"]
    sent = request.args.get("hub.verify_token", "")
    if (token and request.args.get("hub.mode") == "subscribe"
            and hmac.compare_digest(sent, token)):
        return request.args.get("hub.challenge", ""), 200, {"Content-Type": "text/plain"}
    abort(403)


@bp.post("/webhook")
def receive():
    raw = request.get_data()
    if not meta.verify_signature(current_app.config["META_APP_SECRET"], raw,
                                 request.headers.get("X-Hub-Signature-256")):
        abort(403)
    try:
        payload = json.loads(raw)
    except ValueError:
        abort(400)
    processor = current_app.extensions["processor"]
    for event in meta.extract_comments(payload):
        processor.submit(event)
    return "OK", 200  # responde rápido; o tratamento acontece em segundo plano
