"""Marcas e modelos: correção de transcrição, marca sozinha como interesse, áudio confuso (sem modelo)."""

import uuid

import pytest

from app.agente import prompts
from app.agente.grafo import sugestoes
from app.canais.payload import MensagemEntrada
from app.pipeline import guard_entrada, orquestrador
from app.pipeline.midia import normalizar_nomes, vocabulario


@pytest.mark.parametrize("ouvido, certo", [
    ("Estou procurando um build ID Dolphin", "BYD Dolphin"),
    ("tem algum bi wai di?", "BYD"),
    ("quero um baidi", "BYD"),
    ("Estou procurando um BY de Dolphin", "BYD Dolphin"),  # visto na transcrição real
    ("Tem um vê dáblio polo?", "VW polo"),
    ("gosto do agá bê vinte", "HB20"),
    ("e o t cross?", "T-Cross"),
    ("um ônix", "Onix"),
    ("chevrolê tracker", "Chevrolet tracker"),
])
def test_corrige_nomes_falados(ouvido, certo):
    assert certo in normalizar_nomes(ouvido)


def test_biotipo_vira_byd_so_no_audio():
    assert normalizar_nomes("Um biotipo", audio=True) == "Um BYD"
    assert normalizar_nomes("meu biotipo é atlético") == "meu biotipo é atlético"  # texto digitado: não mexe


def test_nao_estraga_texto_normal():
    t = "Quero um SUV até 120 mil para a família"
    assert normalizar_nomes(t) == t


def test_vocabulario_da_transcricao_tem_marcas_e_estoque():
    v = vocabulario()
    assert "BYD" in v and "T-Cross" in v and "Compass" in v and len(v) < 1000


def test_marca_sozinha_no_estoque_mostra_os_carros_dela():
    s = sugestoes({}, ["Toyota"])
    assert "Corolla XEi" in s and "Corolla Cross" in s and "MOSTRE AGORA" in s


def test_marca_fora_do_estoque_avisa():
    s = sugestoes({"tipo_interesse": "Hatch"}, ["Tesla"])
    assert "Tesla, que NÃO está no estoque" in s


def test_audio_curto_fora_do_assunto_pede_para_repetir(monkeypatch):
    monkeypatch.setattr(guard_entrada, "classificar",
                        lambda *a, **k: guard_entrada.Classificacao(categoria="fora_do_assunto", motivo="teste"))
    m = MensagemEntrada(canal="web", cliente_id=f"web-{uuid.uuid4().hex[:6]}", message_id=uuid.uuid4().hex,
                        tipo="audio", transcricao_simulada="Um biotipo")
    res = orquestrador.processar([m])
    assert res.bloqueio_entrada == "audio_incerto"
    assert res.texto_resposta in prompts.FIXAS["audio_incerto"]


def test_texto_fora_do_assunto_continua_bloqueado(monkeypatch):
    monkeypatch.setattr(guard_entrada, "classificar",
                        lambda *a, **k: guard_entrada.Classificacao(categoria="fora_do_assunto", motivo="teste"))
    m = MensagemEntrada(canal="web", cliente_id=f"web-{uuid.uuid4().hex[:6]}", message_id=uuid.uuid4().hex,
                        conteudo="qual a cotação do dólar?")
    res = orquestrador.processar([m])
    assert res.bloqueio_entrada == "fora_do_assunto"
