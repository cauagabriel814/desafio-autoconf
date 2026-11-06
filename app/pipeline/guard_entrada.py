"""Etapa 04: guardrail de entrada. Só bloqueia; o que passa vai para o agente."""

import logging
import re
from typing import Literal

from pydantic import BaseModel

from app import llm
from app.agente import prompts

log = logging.getLogger(__name__)

Categoria = Literal["permitido", "fora_do_assunto", "injecao", "ofensivo"]

_INJECAO = [
    r"ignor[ea]\w*\s+(todas\s+)?(as\s+)?(suas\s+|tuas\s+)?(instru|regras|ordens)",
    r"desconsider\w*\s+(as\s+)?(suas\s+)?(instru|regras)",
    r"(mostr|revel|exib|me\s+(d[aá]|pass)\w*)\w*\s+(o\s+|seu\s+|teu\s+)*(prompt|instru[cç])",
    r"system\s*prompt",
    r"\bjailbreak\b",
    r"\bmodo\s+(dev|desenvolvedor|developer)\b",
    r"(finja|fing[ae]|aja\s+como|atue\s+como)\s+(que\s+)?(voc[eê]\s+)?(é|eh|ser|um|uma)\b",
]


class Classificacao(BaseModel):
    categoria: Categoria
    motivo: str


def regra_injecao(texto: str) -> bool:
    t = texto.lower()
    return any(re.search(p, t) for p in _INJECAO)


def classificar(texto: str, contexto: str, callbacks: list | None = None) -> Classificacao:
    if regra_injecao(texto):
        return Classificacao(categoria="injecao", motivo="regra: padrão de injeção de prompt")
    try:
        modelo = llm.chat(temperature=0).with_structured_output(Classificacao)
        return modelo.invoke(
            prompts.CLASSIFICADOR_ENTRADA.format(contexto=contexto or "(início da conversa)", mensagem=texto),
            config={"callbacks": callbacks or [], "run_name": "guardrail_entrada"},
        )
    except llm.SemChaveOpenAI:
        raise
    except Exception:
        log.exception("Classificador de entrada falhou, liberando a mensagem")
        return Classificacao(categoria="permitido", motivo="falha no classificador")
