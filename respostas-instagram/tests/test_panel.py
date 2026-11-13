from .conftest import csrf_of, login, make_mun, make_rule

ADMIN = ("admin@exemplo.com", "senha-do-admin-123")


def admin_client(app):
    c = app.test_client()
    assert login(c, *ADMIN).status_code == 302
    return c


def make_staff(app, conn, mid, email="equipe@cidade.gov.br", password="senha-da-equipe-1"):
    from werkzeug.security import generate_password_hash
    conn.execute("INSERT INTO users (email, password_hash, role, municipality_id) VALUES (?,?, 'prefeitura', ?)",
                 (email, generate_password_hash(password), mid))
    conn.commit()
    return email, password


def test_painel_exige_login(app):
    r = app.test_client().get("/")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_login_errado_e_certo(app):
    c = app.test_client()
    assert "incorretos" in login(c, "admin@exemplo.com", "errada").get_data(as_text=True)
    assert login(c, *ADMIN).status_code == 302
    assert c.get("/").status_code == 200


def test_post_sem_csrf_e_barrado(app):
    c = admin_client(app)
    assert c.post("/prefeituras/nova", data={"name": "X", "ig_account_id": "12345678"}).status_code == 400
    assert c.post("/logout").status_code == 400


def test_bloqueio_apos_varias_tentativas(app):
    from app import panel
    panel._FAILS.clear()
    c = app.test_client()
    for _ in range(5):
        login(c, "admin@exemplo.com", "errada")
    assert login(c, *ADMIN).status_code == 429
    panel._FAILS.clear()


def test_cadastro_de_prefeitura_criptografa_token_e_nao_o_exibe(app, conn):
    c = admin_client(app)
    r = c.post("/prefeituras/nova", data={
        "name": "Prefeitura de Exemplo", "ig_account_id": "17841499999", "ig_username": "@exemplo",
        "token": "TOKEN-DE-VERDADE-999", "max_replies_per_hour": "10", "_csrf": csrf_of(c, "/prefeituras/nova")})
    assert r.status_code == 302
    mun = conn.execute("SELECT * FROM municipalities WHERE ig_account_id='17841499999'").fetchone()
    assert mun["ig_username"] == "exemplo" and mun["max_replies_per_hour"] == 10
    assert "TOKEN-DE-VERDADE" not in mun["access_token_enc"]
    assert app.extensions["box"].decrypt(mun["access_token_enc"]) == "TOKEN-DE-VERDADE-999"
    page = c.get(f"/prefeituras/{mun['id']}/config").get_data(as_text=True)
    assert "TOKEN-DE-VERDADE" not in page and mun["access_token_enc"] not in page


def test_id_de_conta_invalido_e_duplicado(app, conn):
    c = admin_client(app)
    bad = c.post("/prefeituras/nova", data={"name": "X", "ig_account_id": "abc", "_csrf": csrf_of(c, "/prefeituras/nova")})
    assert "ID numérico" in bad.get_data(as_text=True)
    make_mun(app, conn)
    dup = c.post("/prefeituras/nova", data={"name": "Y", "ig_account_id": "17841400000000001",
                                            "_csrf": csrf_of(c, "/prefeituras/nova")})
    assert "Já existe" in dup.get_data(as_text=True)


def test_equipe_so_ve_a_propria_prefeitura(app, conn):
    a = make_mun(app, conn, name="Cidade A", account="11111111")
    b = make_mun(app, conn, name="Cidade B", account="22222222")
    email, pw = make_staff(app, conn, a["id"])
    c = app.test_client()
    assert login(c, email, pw).status_code == 302
    assert c.get("/").headers["Location"].endswith(f"/prefeituras/{a['id']}")
    assert c.get(f"/prefeituras/{a['id']}/fila").status_code == 200
    for path in (f"/prefeituras/{b['id']}", f"/prefeituras/{b['id']}/fila", f"/prefeituras/{b['id']}/regras",
                 f"/prefeituras/{b['id']}/historico", f"/prefeituras/{b['id']}/config"):
        assert c.get(path).status_code == 404, path
    assert c.get("/usuarios").status_code == 403
    assert c.get("/prefeituras/nova").status_code == 403


def test_equipe_nao_mexe_em_regra_ou_comentario_de_outra_prefeitura(app, conn):
    a = make_mun(app, conn, name="Cidade A", account="11111111")
    b = make_mun(app, conn, name="Cidade B", account="22222222")
    rule_b = make_rule(conn, b["id"])
    conn.execute("INSERT INTO comments (municipality_id, comment_id, text, status) VALUES (?, 'zz', 'oi', 'fila')", (b["id"],))
    conn.commit()
    cid = conn.execute("SELECT id FROM comments").fetchone()[0]
    email, pw = make_staff(app, conn, a["id"])
    c = app.test_client()
    login(c, email, pw)
    tok = csrf_of(c, f"/prefeituras/{a['id']}")
    assert c.post(f"/regras/{rule_b['id']}/excluir", data={"_csrf": tok}).status_code == 404
    assert c.post(f"/comentarios/{cid}/responder", data={"_csrf": tok, "text": "x"}).status_code == 404
    assert conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0] == 1
    assert app.extensions["processor"].sent == []


def test_equipe_nao_consegue_trocar_token_nem_limite(app, conn):
    a = make_mun(app, conn, name="Cidade A", account="11111111", max_replies_per_hour=5)
    email, pw = make_staff(app, conn, a["id"])
    c = app.test_client()
    login(c, email, pw)
    c.post(f"/prefeituras/{a['id']}/config", data={
        "_csrf": csrf_of(c, f"/prefeituras/{a['id']}/config"), "token": "TOKEN-INVASOR",
        "max_replies_per_hour": "500", "ai_enabled": "1", "active": "", "contact_text": "156",
        "extra_sensitive_keywords": "prefeito"})
    row = conn.execute("SELECT * FROM municipalities WHERE id=?", (a["id"],)).fetchone()
    assert app.extensions["box"].decrypt(row["access_token_enc"]) == "TOKEN-SECRETO-123"
    assert row["max_replies_per_hour"] == 5 and row["active"] == 1 and row["ai_enabled"] == 0
    assert row["contact_text"] == "156" and row["extra_sensitive_keywords"] == "prefeito"


def test_criar_regra_e_testar_comentario(app, conn):
    mun = make_mun(app, conn, contact_text="Ouvidoria 156")
    c = admin_client(app)
    path = f"/prefeituras/{mun['id']}/regras"
    r = c.post(path, data={"_csrf": csrf_of(c, path), "name": "Vacina", "keywords": "vacin*",
                           "responses": "Oi {usuario}! Veja {contato}", "priority": "10"})
    assert r.status_code == 302
    rule = conn.execute("SELECT * FROM rules").fetchone()
    assert rule["name"] == "Vacina" and rule["priority"] == 10

    ok = c.post(path, data={"_csrf": csrf_of(c, path), "action": "testar", "text": "Quando é a vacinação?"})
    html = ok.get_data(as_text=True)
    assert "Responderia" in html and "Oi @usuario_exemplo! Veja Ouvidoria 156" in html
    sens = c.post(path, data={"_csrf": csrf_of(c, path), "action": "testar", "text": "Vacina é uma vergonha"})
    assert "Iria para a equipe" in sens.get_data(as_text=True)
    assert app.extensions["processor"].sent == []   # teste nunca envia nada


def test_regra_invalida_nao_e_criada(app, conn):
    mun = make_mun(app, conn)
    c = admin_client(app)
    path = f"/prefeituras/{mun['id']}/regras"
    c.post(path, data={"_csrf": csrf_of(c, path), "name": "Sem resposta", "keywords": "a", "responses": "  "})
    assert conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0] == 0


def test_editar_e_excluir_regra(app, conn):
    mun = make_mun(app, conn)
    rule = make_rule(conn, mun["id"])
    c = admin_client(app)
    tok = csrf_of(c, f"/prefeituras/{mun['id']}/regras")
    # o navegador envia só o campo oculto active=0 quando a caixa "Ativa" está desmarcada
    c.post(f"/regras/{rule['id']}/editar", data={"_csrf": tok, "name": "Novo nome", "keywords": "x",
                                                "responses": "ok", "priority": "5", "active": "0"})
    row = conn.execute("SELECT * FROM rules").fetchone()
    assert row["name"] == "Novo nome" and row["active"] == 0
    c.post(f"/regras/{rule['id']}/editar", data={"_csrf": tok, "name": "Novo nome", "keywords": "x",
                                                "responses": "ok", "priority": "5", "active": ["0", "1"]})
    assert conn.execute("SELECT active FROM rules").fetchone()[0] == 1   # caixa marcada = ativa
    c.post(f"/regras/{rule['id']}/excluir", data={"_csrf": tok})
    assert conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0] == 0


def _comment_in_queue(conn, mid):
    conn.execute("INSERT INTO comments (municipality_id, comment_id, from_username, text, status, reason) "
                 "VALUES (?, 'q1', 'joao', 'vou denunciar', 'fila', 'Assunto sensível')", (mid,))
    conn.commit()
    return conn.execute("SELECT id FROM comments").fetchone()[0]


def test_responder_pela_fila_envia_e_marca_respondido(app, conn):
    mun = make_mun(app, conn)
    cid = _comment_in_queue(conn, mun["id"])
    c = admin_client(app)
    assert "vou denunciar" in c.get(f"/prefeituras/{mun['id']}/fila").get_data(as_text=True)
    tok = csrf_of(c, f"/prefeituras/{mun['id']}/fila")
    c.post(f"/comentarios/{cid}/responder", data={"_csrf": tok, "text": "Olá, João. Pedimos que registre na ouvidoria."})
    assert app.extensions["processor"].sent == [("TOKEN-SECRETO-123", "q1", "Olá, João. Pedimos que registre na ouvidoria.")]
    row = conn.execute("SELECT * FROM comments").fetchone()
    assert row["status"] == "respondido" and row["handled_by"] == "admin@exemplo.com"
    # segunda tentativa não duplica a resposta
    c.post(f"/comentarios/{cid}/responder", data={"_csrf": tok, "text": "de novo"})
    assert len(app.extensions["processor"].sent) == 1


def test_ignorar_item_da_fila(app, conn):
    mun = make_mun(app, conn)
    cid = _comment_in_queue(conn, mun["id"])
    c = admin_client(app)
    c.post(f"/comentarios/{cid}/ignorar", data={"_csrf": csrf_of(c, f"/prefeituras/{mun['id']}/fila")})
    assert conn.execute("SELECT status FROM comments").fetchone()[0] == "ignorado"
    assert app.extensions["processor"].sent == []


def test_historico_filtra_por_status_e_escapa_html(app, conn):
    mun = make_mun(app, conn)
    conn.execute("INSERT INTO comments (municipality_id, comment_id, from_username, text, status) VALUES "
                 "(?, 'h1', 'x', '<script>alert(1)</script>', 'respondido'), (?, 'h2', 'y', 'ignorado aqui', 'ignorado')",
                 (mun["id"], mun["id"]))
    conn.commit()
    c = admin_client(app)
    html = c.get(f"/prefeituras/{mun['id']}/historico?status=respondido").get_data(as_text=True)
    assert "&lt;script&gt;" in html and "<script>alert" not in html and "ignorado aqui" not in html


def test_admin_cria_e_exclui_usuarios(app, conn):
    mun = make_mun(app, conn)
    c = admin_client(app)
    tok = csrf_of(c, "/usuarios")
    c.post("/usuarios", data={"_csrf": tok, "email": "novo@cidade.gov.br", "password": "curta", "role": "prefeitura",
                              "municipality_id": str(mun["id"])})
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1   # senha curta recusada
    c.post("/usuarios", data={"_csrf": tok, "email": "novo@cidade.gov.br", "password": "senha-bem-longa-1",
                              "role": "prefeitura", "municipality_id": str(mun["id"])})
    novo = conn.execute("SELECT * FROM users WHERE email='novo@cidade.gov.br'").fetchone()
    assert novo["municipality_id"] == mun["id"]
    c.post(f"/usuarios/{novo['id']}/excluir", data={"_csrf": tok})
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


def test_cabecalhos_de_seguranca(app):
    r = app.test_client().get("/login")
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
