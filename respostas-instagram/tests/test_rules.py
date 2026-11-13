from app import rules as R


def test_normalize_remove_acentos_e_pontuacao():
    assert R.normalize("  Horário, do POSTO!! ") == "horario do posto"


def test_palavra_exata_nao_casa_variacao_mas_asterisco_casa():
    assert R.find_keyword("quando começa a vacinação?", ["vacina"]) is None
    assert R.find_keyword("quando começa a vacinação?", ["vacin*"]) == "vacin*"


def test_palavra_nao_casa_dentro_de_outra():
    assert R.find_keyword("o preço do pedágio", ["eco"]) is None


def test_frase_com_varias_palavras():
    assert R.find_keyword("Qual o horário de funcionamento?", ["horario de funcionamento"])


def test_sensivel_pega_denuncia_e_variacoes():
    assert R.find_sensitive("Vou fazer uma denúncia disso")
    assert R.find_sensitive("isso é uma VERGONHA")
    assert R.find_sensitive("corrupção na obra")


def test_sensivel_nao_pega_duvida_comum():
    assert R.find_sensitive("Qual o horário do posto de saúde?") is None
    assert R.find_sensitive("Quando abre o processo seletivo?") is None


def test_sensivel_extra_da_prefeitura():
    assert R.find_sensitive("falaram do prefeito", extra="prefeito, vereador") == "prefeito"


def test_dados_pessoais():
    assert R.contains_personal_data("meu CPF é 123.456.789-09")
    assert R.contains_personal_data("me liga (84) 99999-1234")
    assert R.contains_personal_data("escreve pra fulano@email.com")
    assert not R.contains_personal_data("somos 3 pessoas na fila desde as 7")
    assert not R.contains_personal_data("quero o @prefeitura responder")


def test_link_e_sem_letras():
    assert R.contains_link("veja www.golpe.com")
    assert not R.has_letters("❤️🔥👏")
    assert R.has_letters("oi")


def test_match_rule_respeita_ordem_de_prioridade():
    rules = [{"keywords": "posto", "name": "a"}, {"keywords": "posto, saude", "name": "b"}]
    rule, kw = R.match_rule("horário do posto", rules)
    assert rule["name"] == "a" and kw == "posto"


def test_render_response_placeholders():
    out = R.render_response("Oi {usuario}! Fale com {contato}.", "maria", "156")
    assert out == "Oi @maria! Fale com 156."


def test_render_response_sorteia_entre_variacoes():
    seen = {R.render_response("um\ndois\ntrês") for _ in range(100)}
    assert seen == {"um", "dois", "três"}


def test_render_response_vazio():
    assert R.render_response("  \n ") == ""
