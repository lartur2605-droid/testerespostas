from app import meta

from .conftest import ACCOUNT_ID, make_mun, make_rule, payload, post_webhook


def rows(conn):
    return conn.execute("SELECT * FROM comments ORDER BY id").fetchall()


def test_verificacao_do_webhook(app):
    c = app.test_client()
    ok = c.get("/webhook?hub.mode=subscribe&hub.verify_token=token-verificacao&hub.challenge=12345")
    assert ok.status_code == 200 and ok.get_data(as_text=True) == "12345"
    assert c.get("/webhook?hub.mode=subscribe&hub.verify_token=errado&hub.challenge=1").status_code == 403
    assert c.get("/webhook").status_code == 403


def test_assinatura_invalida_e_rejeitada(app, conn):
    make_mun(app, conn)
    c = app.test_client()
    assert post_webhook(c, payload("oi"), secret="outro-segredo").status_code == 403
    assert c.post("/webhook", json=payload("oi")).status_code == 403
    assert rows(conn) == []


def test_fluxo_completo_responde_com_token_descriptografado(app, conn):
    mun = make_mun(app, conn)
    make_rule(conn, mun["id"])
    resp = post_webhook(app.test_client(), payload("Qual o horário do posto?"))
    assert resp.status_code == 200
    proc = app.extensions["processor"]
    assert proc.sent == [("TOKEN-SECRETO-123", "c1", "Olá @maria! Das 7h às 17h.")]
    r = rows(conn)[0]
    assert r["status"] == "respondido" and r["replied_at"] and r["rule_id"]


def test_formato_plano_do_entry_tambem_funciona(app, conn):
    mun = make_mun(app, conn)
    make_rule(conn, mun["id"])
    post_webhook(app.test_client(), payload("horario?", flat=True))
    assert len(app.extensions["processor"].sent) == 1


def test_reenvio_do_mesmo_aviso_nao_responde_duas_vezes(app, conn):
    mun = make_mun(app, conn)
    make_rule(conn, mun["id"])
    c = app.test_client()
    post_webhook(c, payload("horario?"))
    post_webhook(c, payload("horario?"))
    assert len(app.extensions["processor"].sent) == 1 and len(rows(conn)) == 1


def test_comentario_da_propria_conta_e_ignorado_sem_registro(app, conn):
    mun = make_mun(app, conn)
    make_rule(conn, mun["id"])
    post_webhook(app.test_client(), payload("horario?", from_id=ACCOUNT_ID, username="qualquer"))
    post_webhook(app.test_client(), payload("horario?", comment_id="c2", username="Prefeitura_Teste"))
    assert app.extensions["processor"].sent == [] and rows(conn) == []


def test_conta_desconhecida_ou_inativa_nao_faz_nada(app, conn):
    make_mun(app, conn, active=0)
    post_webhook(app.test_client(), payload("horario?"))
    post_webhook(app.test_client(), payload("horario?", comment_id="c2", account="000000000"))
    assert app.extensions["processor"].sent == [] and rows(conn) == []


def test_assunto_sensivel_vai_para_fila_sem_resposta(app, conn):
    mun = make_mun(app, conn)
    make_rule(conn, mun["id"], keywords="posto")
    post_webhook(app.test_client(), payload("Vou fazer denúncia: o posto está uma vergonha"))
    r = rows(conn)[0]
    assert r["status"] == "fila" and "sens" in r["reason"].lower()
    assert app.extensions["processor"].sent == []


def test_dado_pessoal_vai_para_fila(app, conn):
    mun = make_mun(app, conn)
    make_rule(conn, mun["id"], keywords="posto")
    post_webhook(app.test_client(), payload("sobre o posto, meu telefone 84 99999-1234"))
    assert rows(conn)[0]["status"] == "fila"
    assert app.extensions["processor"].sent == []


def test_sem_regra_aprovada_vai_para_fila(app, conn):
    make_mun(app, conn)
    post_webhook(app.test_client(), payload("alguém sabe onde fica a biblioteca?"))
    assert rows(conn)[0]["status"] == "fila"


def test_spam_emoji_e_thread_sao_ignorados(app, conn):
    mun = make_mun(app, conn)
    make_rule(conn, mun["id"])
    c = app.test_client()
    post_webhook(c, payload("horario www.golpe.com", comment_id="a"))
    post_webhook(c, payload("❤️🔥", comment_id="b"))
    post_webhook(c, payload("horario?", comment_id="d", parent_id="c1"))
    assert [r["status"] for r in rows(conn)] == ["ignorado"] * 3
    assert app.extensions["processor"].sent == []


def test_limite_por_hora_manda_excedente_para_fila_com_sugestao(app, conn):
    mun = make_mun(app, conn, max_replies_per_hour=2)
    make_rule(conn, mun["id"])
    c = app.test_client()
    for i in range(3):
        post_webhook(c, payload("horario?", comment_id=f"c{i}"))
    status = [r["status"] for r in rows(conn)]
    assert status == ["respondido", "respondido", "fila"]
    assert "Limite" in rows(conn)[2]["reason"] and rows(conn)[2]["reply_text"]
    assert len(app.extensions["processor"].sent) == 2


def test_erro_da_meta_registra_erro_e_nao_vaza_token(app, conn):
    mun = make_mun(app, conn)
    make_rule(conn, mun["id"])
    proc = app.extensions["processor"]

    def boom(token, comment_id, message):
        raise meta.MetaError("Invalid OAuth access token", 190)

    proc.send = boom
    post_webhook(app.test_client(), payload("horario?"))
    r = rows(conn)[0]
    assert r["status"] == "erro" and "TOKEN-SECRETO" not in r["reason"]


def test_prefeitura_sem_token_gera_erro_claro(app, conn):
    mun = make_mun(app, conn, access_token_enc="")
    make_rule(conn, mun["id"])
    post_webhook(app.test_client(), payload("horario?"))
    r = rows(conn)[0]
    assert r["status"] == "erro" and "token" in r["reason"].lower()


def test_ia_escolhe_so_entre_regras_existentes(app, conn):
    mun = make_mun(app, conn, ai_enabled=1)
    rule = make_rule(conn, mun["id"], keywords="palavra-que-nao-aparece")
    proc = app.extensions["processor"]
    c = app.test_client()

    proc.ai_choose = lambda text, rules: rule["id"]
    post_webhook(c, payload("que horas abre aí?", comment_id="a"))
    proc.ai_choose = lambda text, rules: 9999          # id inventado pela IA
    post_webhook(c, payload("outra pergunta qualquer", comment_id="b"))
    proc.ai_choose = lambda text, rules: None
    post_webhook(c, payload("mais uma pergunta", comment_id="d"))

    assert [r["status"] for r in rows(conn)] == ["respondido", "fila", "fila"]
    assert len(proc.sent) == 1


def test_ia_nao_e_consultada_se_desativada_na_prefeitura(app, conn):
    mun = make_mun(app, conn, ai_enabled=0)
    make_rule(conn, mun["id"], keywords="xyz")
    proc = app.extensions["processor"]
    proc.ai_choose = lambda text, rules: (_ for _ in ()).throw(AssertionError("não deveria chamar"))
    post_webhook(app.test_client(), payload("pergunta sem palavra-chave"))
    assert rows(conn)[0]["status"] == "fila"


def test_recover_stuck_devolve_pendentes_para_a_fila(app, conn):
    from app.processor import recover_stuck
    mun = make_mun(app, conn)
    conn.execute("INSERT INTO comments (municipality_id, comment_id, status) VALUES (?, 'x', 'agendado')",
                 (mun["id"],))
    conn.commit()
    assert recover_stuck(app.config["DATABASE_PATH"]) == 1
    assert conn.execute("SELECT status FROM comments").fetchone()[0] == "fila"


def test_retencao_apaga_comentarios_antigos_mas_preserva_a_fila(app, conn):
    from app.tokens import purge_old_comments
    mun = make_mun(app, conn)
    for cid, status, age in [("velho-ok", "respondido", "-400 days"), ("velho-fila", "fila", "-400 days"),
                             ("novo", "respondido", "-1 days")]:
        conn.execute("INSERT INTO comments (municipality_id, comment_id, status, received_at) "
                     "VALUES (?,?,?, datetime('now', ?))", (mun["id"], cid, status, age))
    conn.commit()
    assert purge_old_comments(app) == 1
    assert {r[0] for r in conn.execute("SELECT comment_id FROM comments")} == {"velho-fila", "novo"}
    app.config["RETENTION_DAYS"] = 0
    assert purge_old_comments(app) == 0


def test_extract_comments_ignora_outros_objetos_e_live(app):
    assert meta.extract_comments({"object": "page", "entry": []}) == []
    live = {"object": "instagram", "entry": [{"id": "1", "changes": [{"field": "live_comments",
            "value": {"id": "z", "text": "oi"}}]}]}
    assert meta.extract_comments(live) == []
