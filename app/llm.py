"""Clientes de modelo e callbacks de monitoramento (Langfuse)."""

import logging
from functools import lru_cache

from langchain_openai import ChatOpenAI
from openai import OpenAI

from app.config import settings


class SemChaveOpenAI(RuntimeError):
    pass


def _checar_chave() -> None:
    if not settings.openai_api_key:
        raise SemChaveOpenAI("OPENAI_API_KEY não configurada no .env")


def chat(temperature: float = 0.4) -> ChatOpenAI:
    _checar_chave()
    return ChatOpenAI(model=settings.openai_model, api_key=settings.openai_api_key, temperature=temperature)


@lru_cache
def openai_client() -> OpenAI:
    _checar_chave()
    return OpenAI(api_key=settings.openai_api_key)


log = logging.getLogger(__name__)


@lru_cache
def _langfuse():
    from langfuse import Langfuse

    return Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
    )


class Rastreio:
    """Um trace do Langfuse por mensagem recebida. Guardrails, agente, tools e juiz entram dentro dele.

    Sem Langfuse configurado, `callbacks` é uma lista vazia e nada é enviado.
    """

    def __init__(self, lead_id: int, cliente_id: str, canal: str):
        self.trace = None
        self.callbacks: list = []
        if not settings.langfuse_ativo:
            return
        try:
            self.trace = _langfuse().trace(
                name=f"mensagem {canal}",
                session_id=f"lead-{lead_id}",
                user_id=cliente_id,
                tags=[canal],
            )
            handler = self.trace.get_langchain_handler(update_parent=False)
            self.callbacks = [handler] if handler is not None else []
        except Exception:
            log.exception("Langfuse indisponível, seguindo sem monitoramento")
            self.trace, self.callbacks = None, []

    def evento(self, nome: str, **dados) -> None:
        if self.trace:
            try:
                self.trace.event(name=nome, metadata=dados)
            except Exception:
                pass

    def fim(self, entrada: str | None, saida: str | None, **metadata) -> None:
        if self.trace:
            try:
                self.trace.update(input=entrada, output=saida, metadata=metadata)
            except Exception:
                pass


def flush() -> None:
    """Envia o que estiver pendente (usado no fim do simulador)."""
    if settings.langfuse_ativo:
        try:
            _langfuse().flush()
        except Exception:
            pass
