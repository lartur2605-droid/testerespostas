"""Painel web: login, prefeituras, regras, fila de revisão humana, histórico e usuários."""
import hmac
import re
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from functools import wraps

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from . import db as dbmod
from . import rules as R
from .processor import decide

bp = Blueprint("panel", __name__)

STATUS_LABELS = {
    "respondido": "Respondido",
    "fila": "Aguardando a equipe",
    "ignorado": "Ignorado",
    "erro": "Erro ao enviar",
    "processando": "Processando",
    "agendado": "Agendado",
}
PAGE_SIZE = 50
_DUMMY_HASH = generate_password_hash("senha-falsa-para-igualar-o-tempo")
_FAILS: dict[str, list[float]] = {}   # tentativas de login por IP e por e-mail
MAX_FAILS, WINDOW = 5, 600


# --- infraestrutura -------------------------------------------------------------------

def get_db():
    if "db" not in g:
        g.db = dbmod.connect(current_app.config["DATABASE_PATH"])
    return g.db


@bp.teardown_app_request
def _close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def current_user():
    if "user" not in g:
        g.user = None
        uid = session.get("uid")
        if uid:
            g.user = get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    return g.user


def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


@bp.app_context_processor
def _inject():
    return {"user": current_user(), "csrf_token": csrf_token, "STATUS_LABELS": STATUS_LABELS,
            "ai_available": bool(current_app.config.get("ANTHROPIC_API_KEY"))}


@bp.before_request
def _guard():
    if request.method == "POST":
        sent, expected = request.form.get("_csrf", ""), session.get("csrf", "")
        if not sent or not expected or not hmac.compare_digest(sent, expected):
            abort(400)
    if request.endpoint != "panel.login" and current_user() is None:
        return redirect(url_for("panel.login"))
    return None


def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **k):
        if current_user()["role"] != "admin":
            abort(403)
        return fn(*a, **k)
    return wrapper


def is_admin() -> bool:
    return current_user()["role"] == "admin"


def load_mun(mid: int):
    """Prefeitura acessível ao usuário atual; senão 404 (não revela que existe)."""
    mun = get_db().execute("SELECT * FROM municipalities WHERE id=?", (mid,)).fetchone()
    if mun is None or (not is_admin() and current_user()["municipality_id"] != mid):
        abort(404)
    return mun


def load_rule(rid: int):
    rule = get_db().execute("SELECT * FROM rules WHERE id=?", (rid,)).fetchone()
    if rule is None:
        abort(404)
    return load_mun(rule["municipality_id"]), rule


def load_comment(cid: int):
    c = get_db().execute("SELECT * FROM comments WHERE id=?", (cid,)).fetchone()
    if c is None:
        abort(404)
    return load_mun(c["municipality_id"]), c


def _int(value, default, lo, hi):
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _str(name: str, maxlen: int = 500) -> str:
    return (request.form.get(name) or "").strip()[:maxlen]


def _throttled(keys: list[str]) -> bool:
    now = time.time()
    for k in keys:
        _FAILS[k] = [t for t in _FAILS.get(k, []) if now - t < WINDOW]
    return any(len(_FAILS[k]) >= MAX_FAILS for k in keys)


# --- login ----------------------------------------------------------------------------

@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = _str("email", 200).lower()
        keys = [f"ip:{request.remote_addr}", f"em:{email}"]
        if _throttled(keys):
            flash("Muitas tentativas. Aguarde alguns minutos e tente de novo.", "erro")
            return render_template("login.html"), 429
        row = get_db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        ok = check_password_hash(row["password_hash"] if row else _DUMMY_HASH,
                                 request.form.get("password", ""))
        if row and ok:
            session.clear()
            session["uid"] = row["id"]
            session.permanent = True
            return redirect(url_for("panel.index"))
        for k in keys:
            _FAILS.setdefault(k, []).append(time.time())
        flash("E-mail ou senha incorretos.", "erro")
    return render_template("login.html")


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("panel.login"))


@bp.route("/conta", methods=["GET", "POST"])
def account():
    if request.method == "POST":
        user = current_user()
        new = request.form.get("new_password", "")
        if not check_password_hash(user["password_hash"], request.form.get("old_password", "")):
            flash("Senha atual incorreta.", "erro")
        elif len(new) < 10:
            flash("A nova senha precisa ter pelo menos 10 caracteres.", "erro")
        else:
            get_db().execute("UPDATE users SET password_hash=? WHERE id=?",
                             (generate_password_hash(new), user["id"]))
            get_db().commit()
            flash("Senha alterada.", "ok")
            return redirect(url_for("panel.account"))
    return render_template("account.html")


# --- prefeituras ------------------------------------------------------------------------

def _mun_stats(conn, mid: int) -> dict:
    q = lambda sql: conn.execute(sql, (mid,)).fetchone()[0]  # noqa: E731
    return {
        "pendentes": q("SELECT COUNT(*) FROM comments WHERE municipality_id=? AND status IN ('fila','erro')"),
        "respondidos_7d": q("SELECT COUNT(*) FROM comments WHERE municipality_id=? AND status='respondido' "
                            "AND replied_at >= datetime('now','-7 days')"),
        "total": q("SELECT COUNT(*) FROM comments WHERE municipality_id=?"),
    }


@bp.get("/")
def index():
    conn = get_db()
    if not is_admin():
        mid = current_user()["municipality_id"]
        if not mid:
            abort(403)
        return redirect(url_for("panel.overview", mid=mid))
    muns = conn.execute("SELECT * FROM municipalities ORDER BY name").fetchall()
    stats = {m["id"]: _mun_stats(conn, m["id"]) for m in muns}
    return render_template("dashboard.html", muns=muns, stats=stats)


@bp.route("/prefeituras/nova", methods=["GET", "POST"])
@admin_required
def new_mun():
    if request.method == "POST":
        name, acc = _str("name", 120), _str("ig_account_id", 40)
        if not name or not re.fullmatch(r"\d{5,30}", acc):
            flash("Informe o nome e o ID numérico da conta do Instagram.", "erro")
        else:
            token = _str("token", 1000)
            box = current_app.extensions["box"]
            try:
                get_db().execute(
                    "INSERT INTO municipalities (name, ig_account_id, ig_username, access_token_enc, "
                    "token_refreshed_at, max_replies_per_hour, contact_text) "
                    "VALUES (?,?,?,?, CASE WHEN ?='' THEN NULL ELSE datetime('now') END, ?, ?)",
                    (name, acc, _str("ig_username", 60).lstrip("@"), box.encrypt(token) if token else "",
                     token, _int(request.form.get("max_replies_per_hour"),
                                 current_app.config["DEFAULT_MAX_REPLIES_PER_HOUR"], 1, 500),
                     _str("contact_text", 300)))
                get_db().commit()
            except sqlite3.IntegrityError:
                flash("Já existe uma prefeitura com esse ID de conta do Instagram.", "erro")
            else:
                mid = get_db().execute("SELECT id FROM municipalities WHERE ig_account_id=?",
                                       (acc,)).fetchone()["id"]
                flash("Prefeitura cadastrada. Agora cadastre as respostas.", "ok")
                return redirect(url_for("panel.rules", mid=mid))
    return render_template("mun_new.html",
                           default_limit=current_app.config["DEFAULT_MAX_REPLIES_PER_HOUR"])


@bp.get("/prefeituras/<int:mid>")
def overview(mid):
    mun = load_mun(mid)
    conn = get_db()
    recent = conn.execute("SELECT * FROM comments WHERE municipality_id=? ORDER BY id DESC LIMIT 10",
                          (mid,)).fetchall()
    token_age = None
    if mun["token_refreshed_at"]:
        try:
            dt = datetime.strptime(mun["token_refreshed_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            token_age = (datetime.now(timezone.utc) - dt).days
        except ValueError:
            pass
    return render_template("overview.html", mun=mun, tab="resumo", stats=_mun_stats(conn, mid),
                           recent=recent, token_age=token_age)


@bp.route("/prefeituras/<int:mid>/config", methods=["GET", "POST"])
def config(mid):
    mun = load_mun(mid)
    if request.method == "POST":
        sets = {"extra_sensitive_keywords": _str("extra_sensitive_keywords", 2000),
                "contact_text": _str("contact_text", 300)}
        if is_admin():
            acc = _str("ig_account_id", 40)
            if not _str("name", 120) or not re.fullmatch(r"\d{5,30}", acc):
                flash("Informe o nome e o ID numérico da conta do Instagram.", "erro")
                return redirect(url_for("panel.config", mid=mid))
            sets.update({
                "name": _str("name", 120), "ig_account_id": acc,
                "ig_username": _str("ig_username", 60).lstrip("@"),
                "max_replies_per_hour": _int(request.form.get("max_replies_per_hour"), 30, 1, 500),
                "ai_enabled": 1 if request.form.get("ai_enabled") else 0,
                "active": 1 if request.form.get("active") else 0,
            })
            token = _str("token", 1000)
            if token:
                sets["access_token_enc"] = current_app.extensions["box"].encrypt(token)
        cols = ", ".join(f"{k}=?" for k in sets)
        try:
            get_db().execute(f"UPDATE municipalities SET {cols}"
                             + (", token_refreshed_at=datetime('now')" if "access_token_enc" in sets else "")
                             + " WHERE id=?", (*sets.values(), mid))
            get_db().commit()
        except sqlite3.IntegrityError:
            flash("Esse ID de conta do Instagram já está em uso por outra prefeitura.", "erro")
        else:
            flash("Configurações salvas.", "ok")
        return redirect(url_for("panel.config", mid=mid))
    return render_template("config.html", mun=mun, tab="config", sensitive_defaults=R.DEFAULT_SENSITIVE)


# --- regras ------------------------------------------------------------------------------

def _rule_form():
    """Valida o formulário de regra. Devolve (dados, erro)."""
    name, keywords, responses = _str("name", 120), _str("keywords", 1000), (request.form.get("responses") or "").strip()
    lines = [ln.strip() for ln in responses.splitlines() if ln.strip()]
    if not name or not R.split_keywords(keywords) or not lines:
        return None, "Preencha o nome, ao menos uma palavra-chave e ao menos uma resposta."
    if any(len(ln) > 2000 for ln in lines):
        return None, "Cada resposta deve ter no máximo 2000 caracteres."
    # Formulário de edição envia active=0 (campo oculto) e, se marcado, active=1. Na criação não há campo: ativa.
    flags = request.form.getlist("active")
    active = 0 if flags and "1" not in flags else 1
    return {"name": name, "keywords": keywords, "responses": "\n".join(lines),
            "priority": _int(request.form.get("priority"), 100, 1, 9999), "active": active}, None


@bp.route("/prefeituras/<int:mid>/regras", methods=["GET", "POST"])
def rules(mid):
    mun = load_mun(mid)
    test = None
    if request.method == "POST" and request.form.get("action") == "testar":
        text = _str("text", 2200)
        active = get_db().execute("SELECT * FROM rules WHERE municipality_id=? AND active=1 "
                                  "ORDER BY priority, id", (mid,)).fetchall()
        d = decide(mun, active, text, from_username="usuario_exemplo")
        test = {"text": text, "decision": d}
    elif request.method == "POST":
        data, err = _rule_form()
        if err:
            flash(err, "erro")
        else:
            get_db().execute("INSERT INTO rules (municipality_id, name, keywords, responses, priority, active) "
                             "VALUES (?,?,?,?,?,?)", (mid, data["name"], data["keywords"], data["responses"],
                                                      data["priority"], data["active"]))
            get_db().commit()
            flash("Regra criada.", "ok")
            return redirect(url_for("panel.rules", mid=mid))
    all_rules = get_db().execute("SELECT * FROM rules WHERE municipality_id=? ORDER BY priority, id",
                                 (mid,)).fetchall()
    return render_template("rules.html", mun=mun, tab="regras", rules=all_rules, test=test)


@bp.post("/regras/<int:rid>/editar")
def rule_edit(rid):
    mun, _ = load_rule(rid)
    data, err = _rule_form()
    if err:
        flash(err, "erro")
    else:
        get_db().execute("UPDATE rules SET name=?, keywords=?, responses=?, priority=?, active=? WHERE id=?",
                         (data["name"], data["keywords"], data["responses"], data["priority"],
                          data["active"], rid))
        get_db().commit()
        flash("Regra atualizada.", "ok")
    return redirect(url_for("panel.rules", mid=mun["id"]))


@bp.post("/regras/<int:rid>/excluir")
def rule_delete(rid):
    mun, _ = load_rule(rid)
    get_db().execute("DELETE FROM rules WHERE id=?", (rid,))
    get_db().commit()
    flash("Regra excluída.", "ok")
    return redirect(url_for("panel.rules", mid=mun["id"]))


# --- fila e histórico ----------------------------------------------------------------------

@bp.get("/prefeituras/<int:mid>/fila")
def queue(mid):
    mun = load_mun(mid)
    items = get_db().execute("SELECT * FROM comments WHERE municipality_id=? AND status IN ('fila','erro') "
                             "ORDER BY id DESC LIMIT 100", (mid,)).fetchall()
    return render_template("queue.html", mun=mun, tab="fila", items=items)


@bp.post("/comentarios/<int:cid>/responder")
def comment_reply(cid):
    mun, c = load_comment(cid)
    text = (request.form.get("text") or "").strip()
    if c["status"] not in ("fila", "erro"):
        flash("Este comentário já foi tratado.", "erro")
    elif not text or len(text) > 2200:
        flash("Escreva uma resposta (até 2200 caracteres).", "erro")
    else:
        ok, msg = current_app.extensions["processor"].reply_now(
            get_db(), mun, c, text, current_user()["email"])
        flash(msg, "ok" if ok else "erro")
    return redirect(url_for("panel.queue", mid=mun["id"]))


@bp.post("/comentarios/<int:cid>/ignorar")
def comment_ignore(cid):
    mun, c = load_comment(cid)
    if c["status"] in ("fila", "erro"):
        get_db().execute("UPDATE comments SET status='ignorado', handled_by=? WHERE id=?",
                         (current_user()["email"], cid))
        get_db().commit()
        flash("Comentário marcado como tratado, sem resposta.", "ok")
    return redirect(url_for("panel.queue", mid=mun["id"]))


@bp.get("/prefeituras/<int:mid>/historico")
def history(mid):
    mun = load_mun(mid)
    status = request.args.get("status", "")
    page = _int(request.args.get("page"), 1, 1, 100000)
    where, args = "municipality_id=?", [mid]
    if status in STATUS_LABELS:
        where += " AND status=?"
        args.append(status)
    rows = get_db().execute(f"SELECT * FROM comments WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?",
                            (*args, PAGE_SIZE + 1, (page - 1) * PAGE_SIZE)).fetchall()
    return render_template("history.html", mun=mun, tab="historico", rows=rows[:PAGE_SIZE],
                           has_next=len(rows) > PAGE_SIZE, page=page, status=status)


# --- usuários ---------------------------------------------------------------------------------

@bp.route("/usuarios", methods=["GET", "POST"])
@admin_required
def users():
    conn = get_db()
    if request.method == "POST":
        email, pw = _str("email", 200).lower(), request.form.get("password", "")
        role = "admin" if request.form.get("role") == "admin" else "prefeitura"
        mid = _int(request.form.get("municipality_id"), 0, 0, 10**9) or None
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            flash("E-mail inválido.", "erro")
        elif len(pw) < 10:
            flash("A senha precisa ter pelo menos 10 caracteres.", "erro")
        elif role == "prefeitura" and not (mid and conn.execute(
                "SELECT 1 FROM municipalities WHERE id=?", (mid,)).fetchone()):
            flash("Escolha a prefeitura desse usuário.", "erro")
        else:
            try:
                conn.execute("INSERT INTO users (email, password_hash, role, municipality_id) VALUES (?,?,?,?)",
                             (email, generate_password_hash(pw), role, mid if role == "prefeitura" else None))
                conn.commit()
                flash("Usuário criado.", "ok")
            except sqlite3.IntegrityError:
                flash("Já existe um usuário com esse e-mail.", "erro")
        return redirect(url_for("panel.users"))
    rows = conn.execute("SELECT u.*, m.name AS mun_name FROM users u "
                        "LEFT JOIN municipalities m ON m.id=u.municipality_id ORDER BY u.email").fetchall()
    muns = conn.execute("SELECT id, name FROM municipalities ORDER BY name").fetchall()
    return render_template("users.html", rows=rows, muns=muns)


@bp.post("/usuarios/<int:uid>/excluir")
@admin_required
def user_delete(uid):
    if uid == current_user()["id"]:
        flash("Você não pode excluir o próprio usuário.", "erro")
    else:
        get_db().execute("DELETE FROM users WHERE id=?", (uid,))
        get_db().commit()
        flash("Usuário excluído.", "ok")
    return redirect(url_for("panel.users"))
