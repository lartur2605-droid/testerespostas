import hashlib
import hmac
import json
import re

import pytest

from app import create_app
from app import db as dbmod
from app.processor import SyncExecutor

APP_SECRET = "segredo-do-app-meta"
ACCOUNT_ID = "17841400000000001"


@pytest.fixture
def app(tmp_path):
    app = create_app({
        "TESTING": True, "DATABASE_PATH": str(tmp_path / "teste.db"),
        "META_APP_SECRET": APP_SECRET, "META_VERIFY_TOKEN": "token-verificacao",
        "SESSION_COOKIE_SECURE": False, "PROXY_FIX": False,
        "REPLY_DELAY_MIN": 0, "REPLY_DELAY_MAX": 0, "ANTHROPIC_API_KEY": "",
        "ADMIN_EMAIL": "admin@exemplo.com", "ADMIN_PASSWORD": "senha-do-admin-123",
    })
    proc = app.extensions["processor"]
    proc.executor = SyncExecutor()
    proc.sent = []

    def fake_send(token, comment_id, message):
        proc.sent.append((token, comment_id, message))
        return "resposta-1"

    proc.send = fake_send
    return app


@pytest.fixture
def conn(app):
    c = dbmod.connect(app.config["DATABASE_PATH"])
    yield c
    c.close()


def make_mun(app, conn, name="Prefeitura de Teste", account=ACCOUNT_ID, **extra):
    box = app.extensions["box"]
    cols = {"name": name, "ig_account_id": account, "ig_username": "prefeitura_teste",
            "access_token_enc": box.encrypt("TOKEN-SECRETO-123"), **extra}
    conn.execute(f"INSERT INTO municipalities ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                 tuple(cols.values()))
    conn.commit()
    return conn.execute("SELECT * FROM municipalities WHERE ig_account_id=?", (account,)).fetchone()


def make_rule(conn, mid, name="Horário", keywords="horario, funciona", responses="Olá {usuario}! Das 7h às 17h.",
              priority=100):
    conn.execute("INSERT INTO rules (municipality_id, name, keywords, responses, priority) VALUES (?,?,?,?,?)",
                 (mid, name, keywords, responses, priority))
    conn.commit()
    return conn.execute("SELECT * FROM rules ORDER BY id DESC LIMIT 1").fetchone()


def payload(text, comment_id="c1", account=ACCOUNT_ID, from_id="999", username="maria", parent_id=None,
            flat=False):
    value = {"id": comment_id, "text": text, "from": {"id": from_id, "username": username},
             "media": {"id": "m1", "media_product_type": "FEED"}}
    if parent_id:
        value["parent_id"] = parent_id
    entry = ({"id": account, "time": 1, "field": "comments", "value": value} if flat else
             {"id": account, "time": 1, "changes": [{"field": "comments", "value": value}]})
    return {"object": "instagram", "entry": [entry]}


def post_webhook(client, body: dict, secret=APP_SECRET):
    raw = json.dumps(body).encode()
    sig = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post("/webhook", data=raw, headers={"X-Hub-Signature-256": sig,
                                                      "Content-Type": "application/json"})


def csrf_of(client, path="/login"):
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="_csrf" value="([^"]+)"', html).group(1)


def login(client, email, password):
    return client.post("/login", data={"email": email, "password": password, "_csrf": csrf_of(client)})
