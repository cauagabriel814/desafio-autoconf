"""Opção "Atender o próprio número": com um número só, a conversa "Mensagem para você" fala com o bot."""

import asyncio

import pytest

from app import configuracao
from app.canais import evolution
from app.config import settings

DONO = "554198000001"
OUTRA = "554198000002"


@pytest.fixture(autouse=True)
def _limpo(monkeypatch):
    monkeypatch.setattr(evolution, "_ids_enviados", {})
    monkeypatch.setattr(evolution, "_textos_enviados", {})
    monkeypatch.setattr(evolution, "_envios_em_andamento", 0)


def _evento(jid: str, texto: str = "#thread oi", de_mim: bool = True, mid: str = "ID1", **key_extra) -> dict:
    return {"event": "messages.upsert", "sender": f"{DONO}@s.whatsapp.net",
            "data": {"key": {"remoteJid": jid, "fromMe": de_mim, "id": mid, **key_extra},
                     "message": {"conversation": texto}, "pushName": "Eu"}}


def _ligar(monkeypatch, valor="on"):
    monkeypatch.setattr(settings, "aceitar_proprio_numero", valor)


def test_desligado_o_bot_ignora_o_proprio_numero(monkeypatch):
    _ligar(monkeypatch, "off")
    assert evolution.converter(_evento(f"{DONO}@s.whatsapp.net")) is None


def test_ligado_atende_a_conversa_com_voce_mesmo(monkeypatch):
    _ligar(monkeypatch)
    m = evolution.converter(_evento(f"{DONO}@s.whatsapp.net"))
    assert m is not None and m.cliente_id == DONO and m.conteudo == "#thread oi"


def test_ligado_nunca_atende_seu_chat_com_outra_pessoa(monkeypatch):
    _ligar(monkeypatch)
    assert evolution.converter(_evento(f"{OUTRA}@s.whatsapp.net", "#thread oi")) is None


def test_ligado_sem_saber_quem_e_o_dono_ignora(monkeypatch):
    _ligar(monkeypatch)
    ev = _evento(f"{DONO}@s.whatsapp.net")
    del ev["sender"]
    assert evolution.converter(ev) is None


def test_conversa_consigo_mesmo_pelo_lid(monkeypatch):
    _ligar(monkeypatch)
    ev = _evento("123456789@lid", senderPn=f"{DONO}@s.whatsapp.net")
    assert evolution.converter(ev) is not None


def test_nao_responde_a_si_mesmo_pelo_id_do_envio(monkeypatch):
    _ligar(monkeypatch)
    monkeypatch.setitem(evolution._ids_enviados, "RESPOSTA1", 9e12)
    assert evolution.converter(_evento(f"{DONO}@s.whatsapp.net", "Olá, tudo bem?", mid="RESPOSTA1")) is None


def test_nao_responde_a_si_mesmo_pelo_texto_enviado(monkeypatch):
    _ligar(monkeypatch)
    monkeypatch.setitem(evolution._textos_enviados, "Olá! Aqui é a Lia", 9e12)
    assert evolution.converter(_evento(f"{DONO}@s.whatsapp.net", "Olá! Aqui é a Lia", mid="OUTRO")) is None


def test_nao_responde_enquanto_um_envio_esta_em_andamento(monkeypatch):
    _ligar(monkeypatch)
    monkeypatch.setattr(evolution, "_envios_em_andamento", 1)  # o webhook chegou antes da resposta do envio
    assert evolution.converter(_evento(f"{DONO}@s.whatsapp.net", "qualquer coisa")) is None


def test_mensagem_de_outra_pessoa_continua_normal_com_a_opcao_desligada(monkeypatch):
    _ligar(monkeypatch, "off")
    m = evolution.converter(_evento(f"{OUTRA}@s.whatsapp.net", "#thread oi", de_mim=False))
    assert m is not None and m.cliente_id == OUTRA


def test_envio_guarda_id_e_texto_para_nao_responder_a_si_mesmo(monkeypatch):
    async def post(caminho, corpo):
        assert evolution._envios_em_andamento == 1  # durante o envio o webhook do próprio número é ignorado
        return {"key": {"id": "ABC123", "fromMe": True}}

    monkeypatch.setattr(evolution, "_post", post)
    asyncio.run(evolution.enviar_texto(DONO, "Oi! Sou a Lia"))
    assert "ABC123" in evolution._ids_enviados and "Oi! Sou a Lia" in evolution._textos_enviados
    assert evolution._envios_em_andamento == 0


def test_opcao_aparece_na_tela_de_config():
    assert "aceitar_proprio_numero" in configuracao.EDITAVEIS
    assert configuracao.EDITAVEIS["aceitar_proprio_numero"][3] == ["on", "off"]
