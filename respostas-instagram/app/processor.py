"""Decide e executa o tratamento de cada comentário recebido pelo webhook."""
import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from . import ai, db, meta
from . import rules as R

log = logging.getLogger(__name__)

MAX_STORED_TEXT = 2200


@dataclass
class Decision:
    action: str            # "reply" | "queue" | "ignore"
    reason: str
    rule_id: int | None = None
    reply_text: str = ""


def decide(mun, rules, text: str, *, from_username: str = "", parent_id: str | None = None,
           ai_choose=None) -> Decision:
    """Função pura: dado o comentário, diz o que fazer. Usada pelo webhook e pelo testador do painel."""
    if parent_id:
        return Decision("ignore", "Resposta dentro de uma conversa (só tratamos comentários principais)")
    if not R.has_letters(text):
        return Decision("ignore", "Comentário sem texto (só emoji ou vazio)")
    if R.contains_link(text):
        return Decision("ignore", "Contém link (possível spam)")
    if R.contains_personal_data(text):
        return Decision("queue", "Contém possível dado pessoal (CPF, telefone ou e-mail): equipe deve tratar")
    hit = R.find_sensitive(text, mun["extra_sensitive_keywords"])
    if hit:
        return Decision("queue", f"Assunto sensível ({hit.rstrip('*')}): equipe deve responder")

    rule, kw = R.match_rule(text, rules)
    via = f"palavra-chave '{kw.rstrip('*')}'" if rule else ""
    if rule is None and mun["ai_enabled"] and ai_choose and rules:
        rid = ai_choose(text, rules)
        rule = next((r for r in rules if r["id"] == rid), None)
        via = "IA"
    if rule is None:
        return Decision("queue", "Nenhuma resposta aprovada serve para este comentário")

    reply = R.render_response(rule["responses"], from_username, mun["contact_text"])
    if not reply:
        return Decision("queue", f"Regra '{rule['name']}' sem texto de resposta")
    return Decision("reply", f"Regra '{rule['name']}' ({via})", rule["id"], reply)


class SyncExecutor:
    """Executa na hora. Usado nos testes."""
    def submit(self, fn, *args, **kwargs):
        fn(*args, **kwargs)


class Processor:
    def __init__(self, config, box, executor=None, send=None, ai_choose=None, sleep=time.sleep):
        self.cfg = config
        self.box = box
        self.executor = executor or ThreadPoolExecutor(max_workers=16, thread_name_prefix="resp")
        self.send = send or self._send_meta
        if ai_choose is None and config.get("ANTHROPIC_API_KEY"):
            ai_choose = ai.make_chooser(config["ANTHROPIC_API_KEY"], config["CLAUDE_MODEL"])
        self.ai_choose = ai_choose
        self.sleep = sleep
        self._lock = threading.Lock()

    # --- envio -----------------------------------------------------------------
    def _send_meta(self, token: str, comment_id: str, message: str) -> str:
        return meta.reply_to_comment(self.cfg["META_GRAPH_HOST"], self.cfg["META_GRAPH_VERSION"],
                                     comment_id, message, token)

    def _token_of(self, mun) -> str:
        if not mun["access_token_enc"]:
            raise meta.MetaError("Prefeitura sem token do Instagram cadastrado")
        return self.box.decrypt(mun["access_token_enc"])

    # --- entrada ---------------------------------------------------------------
    def submit(self, event: dict) -> None:
        self.executor.submit(self._safe_handle, event)

    def _safe_handle(self, event: dict) -> None:
        try:
            self.handle(event)
        except Exception:  # nunca derruba a thread; o comentário fica registrado/recuperável
            log.exception("Erro ao tratar comentário %s", event.get("comment_id"))

    def handle(self, event: dict) -> None:
        conn = db.connect(self.cfg["DATABASE_PATH"])
        try:
            mun = conn.execute(
                "SELECT * FROM municipalities WHERE ig_account_id=? AND active=1",
                (event["ig_account_id"],)).fetchone()
            if mun is None:
                log.info("Webhook de conta desconhecida ou inativa: %s", event["ig_account_id"])
                return
            own_user = (mun["ig_username"] or "").lower()
            if event["from_id"] == mun["ig_account_id"] or (
                    own_user and event["from_username"].lower() == own_user):
                return  # comentário da própria prefeitura (inclui nossas respostas): evita laço

            cur = conn.execute(
                "INSERT OR IGNORE INTO comments (municipality_id, comment_id, media_id, from_id, "
                "from_username, text, status) VALUES (?,?,?,?,?,?, 'processando')",
                (mun["id"], event["comment_id"], event["media_id"], event["from_id"],
                 event["from_username"], event["text"][:MAX_STORED_TEXT]))
            conn.commit()
            if cur.rowcount == 0:
                return  # a Meta reenviou o mesmo aviso
            row_id = cur.lastrowid

            rules = conn.execute(
                "SELECT * FROM rules WHERE municipality_id=? AND active=1 ORDER BY priority, id",
                (mun["id"],)).fetchall()
            d = decide(mun, rules, event["text"], from_username=event["from_username"],
                       parent_id=event["parent_id"], ai_choose=self.ai_choose)

            if d.action != "reply":
                status = "ignorado" if d.action == "ignore" else "fila"
                conn.execute("UPDATE comments SET status=?, reason=? WHERE id=?",
                             (status, d.reason, row_id))
                conn.commit()
                return

            with self._lock:  # confere o limite por hora e reserva a vaga de forma atômica
                used = conn.execute(
                    "SELECT COUNT(*) FROM comments WHERE municipality_id=? AND "
                    "((status='respondido' AND replied_at >= datetime('now','-1 hour')) "
                    "OR status='agendado')", (mun["id"],)).fetchone()[0]
                if used >= mun["max_replies_per_hour"]:
                    conn.execute(
                        "UPDATE comments SET status='fila', reason=?, rule_id=?, reply_text=? WHERE id=?",
                        ("Limite de respostas por hora atingido", d.rule_id, d.reply_text, row_id))
                    conn.commit()
                    return
                conn.execute(
                    "UPDATE comments SET status='agendado', reason=?, rule_id=?, reply_text=? WHERE id=?",
                    (d.reason, d.rule_id, d.reply_text, row_id))
                conn.commit()

            lo, hi = self.cfg["REPLY_DELAY_MIN"], self.cfg["REPLY_DELAY_MAX"]
            if hi > 0:
                self.sleep(random.uniform(min(lo, hi), hi))
            try:
                self.send(self._token_of(mun), event["comment_id"], d.reply_text)
            except meta.MetaError as exc:
                conn.execute("UPDATE comments SET status='erro', reason=? WHERE id=?",
                             (f"Falha ao enviar: {exc}", row_id))
            else:
                conn.execute("UPDATE comments SET status='respondido', replied_at=datetime('now') "
                             "WHERE id=?", (row_id,))
            conn.commit()
        finally:
            conn.close()

    # --- resposta manual (fila do painel) ----------------------------------------
    def reply_now(self, conn, mun, comment, text: str, user_email: str) -> tuple[bool, str]:
        try:
            self.send(self._token_of(mun), comment["comment_id"], text)
        except meta.MetaError as exc:
            return False, f"Não foi possível enviar: {exc}"
        conn.execute(
            "UPDATE comments SET status='respondido', reply_text=?, handled_by=?, "
            "replied_at=datetime('now') WHERE id=?", (text, user_email, comment["id"]))
        conn.commit()
        return True, "Resposta enviada."


def recover_stuck(path: str) -> int:
    """Ao iniciar o servidor, tarefas que estavam em andamento foram perdidas: devolve para a fila."""
    conn = db.connect(path)
    try:
        cur = conn.execute(
            "UPDATE comments SET status='fila', reason='Interrompido por reinício do sistema' "
            "WHERE status IN ('processando','agendado')")
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()
