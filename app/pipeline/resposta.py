"""Etapa 07 (parte 1): define o formato e gera o áudio quando o cliente mandou áudio."""

import logging
import re
import uuid

from app import llm
from app.agente import prompts
from app.config import settings

log = logging.getLogger(__name__)


def versao_para_audio(texto: str, callbacks: list | None = None) -> str:
    r = llm.chat(temperature=0.3).invoke(
        prompts.VERSAO_AUDIO.format(texto=texto), config={"callbacks": callbacks or [], "run_name": "versao_audio"}
    )
    return r.content.strip()


def gerar_audio(texto_falado: str) -> str | None:
    """Gera o áudio em opus (formato de voz do WhatsApp). Devolve o nome do arquivo em data/audios."""
    nome = f"{uuid.uuid4().hex}.ogg"
    destino = settings.audios_dir / nome
    try:
        kwargs = dict(model=settings.openai_tts_model, voice=settings.openai_tts_voice, input=texto_falado,
                      response_format="opus")
        if settings.openai_tts_model != "tts-1":
            kwargs["instructions"] = prompts.INSTRUCOES_VOZ
        with llm.openai_client().audio.speech.with_streaming_response.create(**kwargs) as r:
            r.stream_to_file(destino)
        return nome
    except Exception:
        log.exception("Falha ao gerar áudio, respondendo só em texto")
        return None


def em_baloes(texto: str, maximo: int = 3) -> list[str]:
    """Quebra a resposta em balões, como uma pessoa manda no WhatsApp (parágrafos separados por linha em branco).

    Uma lista de carros fica inteira num balão só. No máximo `maximo` balões: o que sobrar vai no último.
    """
    partes = [p.strip() for p in re.split(r"\n\s*\n", texto.strip()) if p.strip()]
    if len(partes) <= 1:
        return [texto.strip()] if texto.strip() else []
    # junta linhas de lista que ficaram separadas por linha em branco
    juntas: list[str] = []
    for p in partes:
        if juntas and p.lstrip().startswith(("-", "•")) and juntas[-1].lstrip().startswith(("-", "•")):
            juntas[-1] += "\n" + p
        else:
            juntas.append(p)
    if len(juntas) > maximo:
        juntas = juntas[: maximo - 1] + ["\n\n".join(juntas[maximo - 1:])]
    return juntas


def tempo_digitando(texto: str) -> int:
    """Milissegundos de "digitando…" antes de cada balão: proporcional ao tamanho, entre 0,8 e 4 s."""
    return max(800, min(4000, 500 + 22 * len(texto)))


def tem_detalhes(texto: str) -> bool:
    """Respostas com preço ou lista vão também por escrito, além do áudio."""
    return "R$" in texto or texto.count("\n") >= 2
