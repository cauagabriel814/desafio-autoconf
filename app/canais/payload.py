"""Mensagem padrão: todo canal converte o que recebe para este formato (etapa 01)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Canal = Literal["whatsapp", "web", "terminal"]
Tipo = Literal["texto", "audio", "imagem", "outro"]  # outro = documento, vídeo, figurinha, formato não suportado


class MensagemEntrada(BaseModel):
    canal: Canal
    cliente_id: str
    message_id: str
    tipo: Tipo = "texto"
    conteudo: str = ""  # texto, ou legenda da imagem
    midia_b64: str | None = None
    midia_mime: str | None = None
    nome: str | None = None
    recebido_em: datetime = Field(default_factory=datetime.now)
    # Só para o simulador: pula a transcrição e usa este texto (vazio = áudio com ruído)
    transcricao_simulada: str | None = None


class Resultado(BaseModel):
    """O que o pipeline devolve para o canal entregar."""

    lead_id: int | None = None
    texto_resposta: str = ""  # resposta completa (também quando vai em áudio)
    textos: list[str] = []  # textos a enviar ao cliente
    audio_path: str | None = None
    tipo_resposta: Literal["texto", "audio", "nenhuma"] = "texto"
    transferido: bool = False
    status_lead: str = "bot"
    # Diagnóstico (usado pelo simulador e pelos logs)
    ignorado: str | None = None  # motivo de não responder
    bloqueio_entrada: str | None = None
    bloqueios_saida: list[str] = []
    tools_chamadas: list[str] = []
    texto_cliente: str | None = None
