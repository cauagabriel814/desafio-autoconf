"""#reset e #parar (sem chamar o modelo)."""

import uuid

from app.canais.payload import MensagemEntrada
from app.db import repo
from app.pipeline import orquestrador


def _num():
    return f"5511{uuid.uuid4().int % 10**9:09d}"


def _msg(texto, cliente, canal="whatsapp", tipo="texto"):
    return MensagemEntrada(canal=canal, cliente_id=cliente, message_id=uuid.uuid4().hex, tipo=tipo, conteudo=texto)


def _lead(cliente, canal="whatsapp"):
    return repo.obter_ou_criar_lead(canal, cliente)


def test_parar_funciona_sem_thread():
    n = _num()
    conf = orquestrador.executar_comando(_msg("#parar", n))
    assert conf and "#thread" in conf
    assert _lead(n).status == "parado"


def test_parado_nao_responde_e_grava():
    n = _num()
    orquestrador.executar_comando(_msg("#parar", n))
    assert orquestrador.aceitar(_msg("oi, tem SUV?", n)) == (False, "parado")
    assert orquestrador.aceitar(_msg("", n, tipo="audio")) == (False, "parado")
    msgs = repo.detalhe_lead(_lead(n).id)["mensagens"]
    assert [m["status"] for m in msgs[-2:]] == ["ignorada", "ignorada"]


def test_thread_religa_na_mesma_conversa():
    n = _num()
    lead = _lead(n)
    repo.atualizar_ficha(lead.id, {"preco_max": 120000})
    orquestrador.executar_comando(_msg("#parar", n))
    assert orquestrador.aceitar(_msg("#thread voltei", n)) == (True, None)
    depois = _lead(n)
    assert depois.id == lead.id and depois.status == "bot" and depois.ficha["preco_max"] == 120000


def test_comandos_nao_valem_na_tela():
    c = f"web-{uuid.uuid4().hex[:6]}"
    assert orquestrador.executar_comando(_msg("#parar", c, canal="web")) is None
    assert orquestrador.executar_comando(_msg("#reset", c, canal="web")) is None
    assert _lead(c, canal="web").status == "bot"
    assert orquestrador.aceitar(_msg("oi", c, canal="web")) == (True, None)


def test_comandos_desligados_com_test_off(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "test", "off")
    n = _num()
    assert orquestrador.executar_comando(_msg("#parar", n)) is None
    assert orquestrador.executar_comando(_msg("#reset", n)) is None
    assert _lead(n).status == "bot"


def test_test_aceita_variacoes(monkeypatch):
    from app.config import settings

    for valor, ativo in [("on", True), ("ON", True), ("true", True), ("off", False), ("", False)]:
        monkeypatch.setattr(settings, "test", valor)
        assert settings.comandos_ativos is ativo


def test_reset_arquiva_e_comeca_do_zero():
    n = _num()
    antigo = _lead(n)
    repo.atualizar_ficha(antigo.id, {"preco_max": 90000})
    conf = orquestrador.executar_comando(_msg("#reset", n))
    assert conf.startswith("Conversa reiniciada") and "#thread" in conf
    arquivado = repo.obter_lead(antigo.id)
    assert arquivado.status == "arquivado" and arquivado.cliente_id.startswith(n + "~")
    novo = _lead(n)
    assert novo.id != antigo.id and novo.ficha == {} and novo.status == "bot"


def test_reset_depois_de_parar():
    n = _num()
    orquestrador.executar_comando(_msg("#parar", n))
    orquestrador.executar_comando(_msg("#reset", n))
    assert _lead(n).status == "bot"


def test_reset_duas_vezes_seguidas():
    n = _num()
    orquestrador.executar_comando(_msg("#reset", n))
    orquestrador.executar_comando(_msg("#reset", n))
    assert _lead(n).status == "bot"


def test_reset_tira_cliente_do_vendedor():
    n = _num()
    lead = _lead(n)
    repo.definir_status(lead.id, "humano")
    orquestrador.executar_comando(_msg("#reset", n))
    assert _lead(n).status == "bot"


def test_comando_repetido_pelo_webhook_nao_confirma_de_novo():
    n = _num()
    m = _msg("#parar", n)
    assert orquestrador.executar_comando(m)
    assert orquestrador.executar_comando(m) == ""


def test_texto_normal_nao_e_comando():
    assert orquestrador.executar_comando(_msg("#thread quero parar de pagar aluguel", _num())) is None


def test_reconhece_so_cumprimento():
    from app.pipeline.orquestrador import _SO_CUMPRIMENTO

    for t in ("Oi", "oii", "Olá, boa tarde", "Boa noite!", "Oi, tudo bem?", "e aí", "Olá, tudo bem com você?", "Opa 👋", "ólá", "Ólá!"):
        assert _SO_CUMPRIMENTO.match(t), t
    for t in ("Oi, queria ver uns carros", "Boa tarde, tem SUV?", "Oi, quanto tá o Civic?", "Quero um carro"):
        assert not _SO_CUMPRIMENTO.match(t), t


def test_texto_da_saudacao():
    from app.agente.prompts import saudacao
    from app.config import settings

    s = saudacao("Oi")
    assert s.startswith(("Bom dia!", "Boa tarde!", "Boa noite!"))
    assert f"Aqui é a {settings.nome_atendente}, da {settings.nome_loja}" in s and s.endswith("Como posso te ajudar?")
    assert "carro" not in s.lower()
    assert "Tudo ótimo por aqui!" in saudacao("Oi, tudo bem?")
    # usa o mesmo período que o cliente disse, independente do relógio
    assert saudacao("Olá, boa tarde").startswith("Boa tarde!")
    assert saudacao("boa noite!").startswith("Boa noite!")
    assert saudacao("Bom dia").startswith("Bom dia!")


def test_saudacao_no_inicio_da_conversa():
    c = f"web-{uuid.uuid4().hex[:6]}"
    r = orquestrador.processar([_msg("Oi", c, canal="web")])
    assert "Como posso te ajudar?" in r.texto_resposta and r.bloqueio_entrada is None


def test_thread_sozinho_vira_oi():
    texto, tipo, _ = orquestrador._normalizar([_msg("#thread", _num())])
    assert texto == "Oi" and tipo == "texto"
