"""Renovação automática dos tokens do Instagram (duram 60 dias; renovamos a cada ~20)."""
import logging
import threading
import time

from . import db, meta

log = logging.getLogger(__name__)


def refresh_due(app, max_age_days: int = 20) -> int:
    cfg = app.config
    box = app.extensions["box"]
    conn = db.connect(cfg["DATABASE_PATH"])
    done = 0
    try:
        rows = conn.execute(
            "SELECT id, name, access_token_enc FROM municipalities "
            "WHERE active=1 AND access_token_enc != '' AND "
            "(token_refreshed_at IS NULL OR token_refreshed_at < datetime('now', ?))",
            (f"-{int(max_age_days)} days",)).fetchall()
        for r in rows:
            try:
                new = meta.refresh_token(cfg["META_GRAPH_HOST"], box.decrypt(r["access_token_enc"]))
            except (meta.MetaError, RuntimeError) as exc:
                log.warning("Não renovou token de %s: %s", r["name"], exc)
                continue
            conn.execute("UPDATE municipalities SET access_token_enc=?, "
                         "token_refreshed_at=datetime('now') WHERE id=?", (box.encrypt(new), r["id"]))
            conn.commit()
            done += 1
    finally:
        conn.close()
    return done


def purge_old_comments(app) -> int:
    """LGPD: apaga comentários mais antigos que RETENTION_DAYS (0 desliga). Mantém a fila pendente."""
    days = int(app.config["RETENTION_DAYS"])
    if days <= 0:
        return 0
    conn = db.connect(app.config["DATABASE_PATH"])
    try:
        cur = conn.execute("DELETE FROM comments WHERE received_at < datetime('now', ?) "
                           "AND status NOT IN ('fila','erro')", (f"-{days} days",))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def start_background_jobs(app) -> None:
    def loop():
        while True:
            try:
                purge_old_comments(app)
                refresh_due(app)
            except Exception:
                log.exception("Erro nas tarefas de manutenção")
            time.sleep(6 * 3600)

    threading.Thread(target=loop, name="token-refresh", daemon=True).start()
