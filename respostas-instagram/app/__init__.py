"""Respostas automáticas em comentários do Instagram para várias prefeituras."""
import logging
from datetime import datetime, timedelta, timezone

import click
from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash

from . import db as dbmod
from .config import Config
from .crypto import TokenBox, generate_key
from .processor import Processor


def _localtime_filter(app):
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(app.config["TIMEZONE"])
    except Exception:  # sem base de fusos no sistema: Brasil sem horário de verão = UTC-3
        tz = timezone(timedelta(hours=-3))

    def localtime(value):
        if not value:
            return ""
        try:
            dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return value
        return dt.astimezone(tz).strftime("%d/%m/%Y %H:%M")

    return localtime


def create_app(overrides: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)
    testing = bool(app.config.get("TESTING"))

    if not app.config["SECRET_KEY"]:
        if not testing:
            raise RuntimeError("SECRET_KEY não definida. Gere os valores com: python -m app.keys")
        app.config["SECRET_KEY"] = "chave-de-teste"
    if not app.config["ENCRYPTION_KEY"]:
        if not testing:
            raise RuntimeError("ENCRYPTION_KEY não definida. Gere os valores com: python -m app.keys")
        app.config["ENCRYPTION_KEY"] = generate_key()

    app.permanent_session_lifetime = timedelta(hours=8)
    app.config["MAX_CONTENT_LENGTH"] = 1_000_000
    if app.config["PROXY_FIX"]:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    dbmod.init_db(app.config["DATABASE_PATH"])

    box = TokenBox(app.config["ENCRYPTION_KEY"])
    app.extensions["box"] = box
    app.extensions["processor"] = Processor(app.config, box)
    app.jinja_env.filters["localtime"] = _localtime_filter(app)

    from . import panel, webhook
    app.register_blueprint(webhook.bp)
    app.register_blueprint(panel.bp)

    @app.after_request
    def security_headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; form-action 'self'; frame-ancestors 'none'")
        resp.headers["Cache-Control"] = "no-store"
        return resp

    _bootstrap_admin(app)
    _register_cli(app)
    return app


def _bootstrap_admin(app: Flask) -> None:
    email, password = app.config["ADMIN_EMAIL"], app.config["ADMIN_PASSWORD"]
    if not email or not password:
        return
    conn = dbmod.connect(app.config["DATABASE_PATH"])
    try:
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            conn.execute("INSERT INTO users (email, password_hash, role) VALUES (?,?, 'admin')",
                         (email.strip().lower(), generate_password_hash(password)))
            conn.commit()
    finally:
        conn.close()


def _register_cli(app: Flask) -> None:
    @app.cli.command("create-admin")
    @click.argument("email")
    @click.password_option()
    def create_admin(email, password):
        """Cria (ou troca a senha de) um administrador."""
        conn = dbmod.connect(app.config["DATABASE_PATH"])
        try:
            conn.execute(
                "INSERT INTO users (email, password_hash, role) VALUES (?,?, 'admin') "
                "ON CONFLICT(email) DO UPDATE SET password_hash=excluded.password_hash, role='admin'",
                (email.strip().lower(), generate_password_hash(password)))
            conn.commit()
        finally:
            conn.close()
        click.echo("Administrador salvo.")

    @app.cli.command("refresh-tokens")
    def refresh_tokens():
        """Renova agora os tokens do Instagram que estão para vencer."""
        from .tokens import refresh_due
        click.echo(f"{refresh_due(app)} token(s) renovado(s).")
