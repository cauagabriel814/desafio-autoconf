"""A instância do WhatsApp nasce criada na subida (só falta ler o QR code)."""

import asyncio

import httpx

from app.canais import evolution
from app.config import settings


def _erro(status: int) -> httpx.HTTPStatusError:
    resp = httpx.Response(status, request=httpx.Request("POST", "http://evolution/instance/create"))
    return httpx.HTTPStatusError("erro", request=resp.request, response=resp)


def _com_chave(monkeypatch):
    monkeypatch.setattr(settings, "evolution_api_key", "chave-de-teste")
    monkeypatch.setattr(settings, "evolution_instance", "autoconf")


def test_cria_a_instancia_quando_a_evolution_responde(monkeypatch):
    _com_chave(monkeypatch)
    enviados = []

    async def post(caminho, json):
        enviados.append((caminho, json))
        return {}

    monkeypatch.setattr(evolution, "_post", post)
    assert asyncio.run(evolution.garantir_instancia(tentativas=2, espera=0)) is True
    assert enviados[0][0] == "/instance/create" and enviados[0][1]["instanceName"] == "autoconf"


def test_instancia_que_ja_existe_conta_como_pronta(monkeypatch):
    _com_chave(monkeypatch)

    async def post(*_a, **_k):
        raise _erro(403)  # a Evolution responde 403 quando o nome já existe

    monkeypatch.setattr(evolution, "_post", post)
    assert asyncio.run(evolution.garantir_instancia(tentativas=2, espera=0)) is True


def test_espera_a_evolution_subir_e_tenta_de_novo(monkeypatch):
    _com_chave(monkeypatch)
    chamadas = []

    async def post(*_a, **_k):
        chamadas.append(1)
        if len(chamadas) < 3:
            raise httpx.ConnectError("ainda subindo")
        return {}

    monkeypatch.setattr(evolution, "_post", post)
    assert asyncio.run(evolution.garantir_instancia(tentativas=5, espera=0)) is True
    assert len(chamadas) == 3


def test_desiste_sem_quebrar_se_a_evolution_nao_sobe(monkeypatch):
    _com_chave(monkeypatch)

    async def post(*_a, **_k):
        raise httpx.ConnectError("fora do ar")

    monkeypatch.setattr(evolution, "_post", post)
    assert asyncio.run(evolution.garantir_instancia(tentativas=2, espera=0)) is False


def test_sem_chave_nao_tenta(monkeypatch):
    monkeypatch.setattr(settings, "evolution_api_key", "")
    assert asyncio.run(evolution.garantir_instancia(tentativas=2, espera=0)) is False
