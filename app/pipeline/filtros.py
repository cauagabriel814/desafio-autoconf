"""Etapa 02: decide se a mensagem é para o bot.

1. ruído e duplicadas  2. #thread  3. já está com humano  (4. agrupar fica no canal)
"""

import re
from collections import deque

from app.canais.payload import MensagemEntrada
from app.config import settings
from app.db import repo
from app.db.modelos import Lead

_vistos: deque[str] = deque(maxlen=2000)


def duplicada(message_id: str) -> bool:
    if message_id in _vistos:
        return True
    _vistos.append(message_id)
    return repo.mensagem_ja_existe(message_id)


def contem_gatilho(texto: str) -> bool:
    kw = settings.trigger_keyword.strip()
    return bool(kw) and kw.lower() in (texto or "").lower()


def remover_gatilho(texto: str) -> str:
    kw = settings.trigger_keyword.strip()
    if not kw:
        return texto.strip()
    return re.sub(re.escape(kw), "", texto or "", flags=re.IGNORECASE).strip()


def checar_gatilho(msg: MensagemEntrada, lead: Lead) -> bool:
    """O #thread vale só para o WhatsApp.

    Texto precisa ter a palavra-chave. Áudio e imagem sem legenda não têm como trazê-la,
    então passam se o cliente mandou #thread nos últimos minutos (sessão aberta).
    """
    if msg.canal != "whatsapp" or not settings.trigger_keyword.strip():
        return True
    if contem_gatilho(msg.conteudo):
        repo.abrir_sessao(lead.id)
        return True
    if msg.tipo in ("audio", "imagem", "outro") and repo.sessao_ativa(lead):
        return True
    return False


def com_humano(lead: Lead) -> bool:
    return lead.status == "humano"


def parado(lead: Lead) -> bool:
    return lead.status == "parado"


def _tem(palavra: str, texto: str) -> bool:
    palavra = palavra.strip().lower()
    return bool(palavra) and palavra in (texto or "").lower()


def comando(texto: str) -> str | None:
    """'reset', 'parar' ou None. O reset ganha se vierem os dois."""
    if _tem(settings.reset_keyword, texto):
        return "reset"
    if _tem(settings.stop_keyword, texto):
        return "parar"
    return None
