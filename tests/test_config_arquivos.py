"""Configuração do agente (tela/API) e tipos de arquivo (sem chamar o modelo)."""

import io
import uuid

import pytest
from fastapi.testclient import TestClient

from app import configuracao
from app.agente import prompts
from app.canais import evolution
from app.config import settings
from app.main import app
from app.pipeline import orquestrador
from app.pipeline.midia import tipo_suportado


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def limpa_config():
    yield
    configuracao.restaurar()


# ---------- configuração ----------

def test_config_mostra_editaveis_e_consulta(client):
    j = client.get("/config").json()
    chaves = {e["chave"] for e in j["editaveis"]}
    assert {"modo_resposta", "nome_atendente", "trigger_keyword", "test", "agrupar_segundos"} <= chaves
    consulta = {c["chave"] for c in j["so_consulta"]}
    assert {"openai_model", "openai_stt_model", "openai_tts_model", "langfuse_host"} <= consulta
    assert any(b["nome"] == "langgraph" for b in j["bibliotecas"]) and j["python"]


def test_modelos_nao_podem_ser_mudados(client):
    r = client.put("/config", json={"openai_model": "gpt-5"})
    assert r.status_code == 400 and "não dá para mudar" in r.json()["detail"]


def test_mudar_vale_na_hora_e_restaurar_volta(client):
    r = client.put("/config", json={"nome_atendente": "Bia", "modo_resposta": "sempre_texto"})
    assert r.status_code == 200 and settings.nome_atendente == "Bia" and settings.modo_resposta == "sempre_texto"
    client.post("/config/restaurar")
    assert settings.nome_atendente == configuracao.PADROES["nome_atendente"]
    assert settings.modo_resposta == "espelhar"


def test_valida_valores(client):
    assert client.put("/config", json={"modo_resposta": "as_vezes"}).status_code == 400
    assert client.put("/config", json={"agrupar_segundos": 99}).status_code == 400
    assert client.put("/config", json={"nome_atendente": ""}).status_code == 400
    # tudo ou nada: um valor inválido não grava os outros
    client.put("/config", json={"nome_atendente": "Ana", "historico_n": 0})
    assert settings.nome_atendente != "Ana"


def test_config_persiste_no_banco():
    configuracao.salvar({"nome_loja": "Loja Teste"})
    settings.nome_loja = "outro valor"  # simula reinício
    configuracao.carregar()
    assert settings.nome_loja == "Loja Teste"


def test_tela_config(client):
    html = client.get("/painel/config").text
    assert "Quando o agente responde em áudio" in html and "Bibliotecas" in html and "gpt-4o-mini" in html


@pytest.mark.parametrize("modo, origem, audio", [
    ("espelhar", "audio", True), ("espelhar", "texto", False), ("espelhar", "imagem", False),
    ("sempre_texto", "audio", False), ("sempre_audio", "texto", True), ("texto_e_audio", "texto", True),
])
def test_regra_de_audio(modo, origem, audio):
    settings.modo_resposta = modo
    assert orquestrador.responder_em_audio(origem) is audio


# ---------- tipos de arquivo ----------

@pytest.mark.parametrize("mime, tamanho, tipo", [
    ("audio/ogg; codecs=opus", 5000, "audio"), ("audio/webm", 5000, "audio"), ("audio/mpeg", 5000, "audio"),
    ("audio/mp4", 5000, "audio"), ("audio/wav", 5000, "audio"), ("audio/flac", 5000, "audio"),
    ("image/jpeg", 5000, "imagem"), ("image/png", 5000, "imagem"), ("image/webp", 5000, "imagem"),
    ("image/gif", 5000, "imagem"),
    ("audio/aac", 5000, "outro"), ("image/heic", 5000, "outro"), ("application/pdf", 5000, "outro"),
    ("video/mp4", 5000, "outro"), ("text/plain", 10, "outro"), ("", 10, "outro"),
    ("image/png", 21 * 1024 * 1024, "outro"), ("audio/ogg", 26 * 1024 * 1024, "outro"),
])
def test_tipo_suportado(mime, tamanho, tipo):
    assert tipo_suportado(mime, tamanho)[0] == tipo


@pytest.mark.parametrize("nome, mime, dados", [
    ("doc.pdf", "application/pdf", b"%PDF-1.4" + b"0" * 500),
    ("foto.heic", "image/heic", b"\x00" * 500),
    ("v.mp4", "video/mp4", b"\x00" * 500),
    ("vazio.ogg", "audio/ogg", b""),
])
def test_chat_com_arquivo_nao_suportado_responde_com_jeito(client, nome, mime, dados):
    r = client.post("/chat", data={"cliente_id": f"web-{uuid.uuid4().hex[:6]}"},
                    files={"arquivo": (nome, io.BytesIO(dados), mime)})
    assert r.status_code == 200
    assert " ".join(r.json()["textos"]) in prompts.FIXAS["arquivo_nao_suportado"] or any(
        t in prompts.FIXAS["arquivo_nao_suportado"] for t in r.json()["textos"])


def test_documento_no_whatsapp_vira_outro():
    body = {"event": "messages.upsert", "data": {
        "key": {"remoteJid": "5511999990000@s.whatsapp.net", "id": "DOC1"},
        "message": {"documentMessage": {"fileName": "rg.pdf", "caption": "#thread meu documento"}}}}
    m = evolution.converter(body)
    assert m.tipo == "outro" and "meu documento" in m.conteudo


def test_figurinha_no_whatsapp_vira_outro():
    body = {"event": "messages.upsert", "data": {
        "key": {"remoteJid": "5511999990000@s.whatsapp.net", "id": "ST1"}, "message": {"stickerMessage": {}}}}
    assert evolution.converter(body).tipo == "outro"
