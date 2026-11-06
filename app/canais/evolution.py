"""Canal WhatsApp via Evolution API v2: entrada (webhook), agrupamento e saída."""

import asyncio
import base64
import logging
import time
from typing import Awaitable, Callable

import httpx
from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from app.canais.payload import MensagemEntrada, Resultado
from app.config import settings

log = logging.getLogger(__name__)
router = APIRouter()


# ---------- Cliente HTTP da Evolution ----------

def _headers() -> dict:
    return {"apikey": settings.evolution_api_key, "Content-Type": "application/json"}


def _url(caminho: str) -> str:
    return f"{settings.evolution_api_url.rstrip('/')}{caminho}"


async def _post(caminho: str, json: dict) -> dict:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(_url(caminho), json=json, headers=_headers())
        r.raise_for_status()
        return r.json() if r.content else {}


async def _get(caminho: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(_url(caminho), headers=_headers())
        r.raise_for_status()
        return r.json()


# O que o próprio bot enviou: com "Atender o próprio número" ligado, a resposta da Lia volta como mensagem do próprio
# número, e ela não pode responder a si mesma. Guardamos o id e o texto de cada envio, e ignoramos mensagens do próprio
# número enquanto um envio está em andamento (o webhook pode chegar antes da resposta do envio).
_ids_enviados: dict[str, float] = {}
_textos_enviados: dict[str, float] = {}
_envios_em_andamento = 0
_VALIDADE_ENVIADOS = 600  # segundos


def _limpar_enviados() -> None:
    corte = time.time() - _VALIDADE_ENVIADOS
    for memoria in (_ids_enviados, _textos_enviados):
        for k in [k for k, quando in memoria.items() if quando < corte]:
            memoria.pop(k, None)


async def _enviar(caminho: str, corpo: dict, texto: str | None = None) -> None:
    global _envios_em_andamento
    _envios_em_andamento += 1
    try:
        if texto:
            _textos_enviados[texto.strip()] = time.time()
        resposta = await _post(caminho, corpo)
        if isinstance(resposta, dict) and (mid := (resposta.get("key") or {}).get("id")):
            _ids_enviados[mid] = time.time()
    finally:
        _envios_em_andamento -= 1
        _limpar_enviados()


async def enviar_texto(numero: str, texto: str, digitando_ms: int = 0) -> None:
    """`digitando_ms`: a Evolution mostra "digitando…" por esse tempo antes de entregar o balão."""
    corpo = {"number": numero, "text": texto}
    if digitando_ms:
        corpo["delay"] = digitando_ms
    await _enviar(f"/message/sendText/{settings.evolution_instance}", corpo, texto)


async def enviar_audio(numero: str, audio_nome: str) -> None:
    dados = (settings.audios_dir / audio_nome).read_bytes()
    await _enviar(
        f"/message/sendWhatsAppAudio/{settings.evolution_instance}",
        {"number": numero, "audio": base64.b64encode(dados).decode()},
    )


async def baixar_midia(message_id: str) -> tuple[str | None, str | None]:
    try:
        r = await _post(
            f"/chat/getBase64FromMediaMessage/{settings.evolution_instance}",
            {"message": {"key": {"id": message_id}}, "convertToMp4": False},
        )
        return r.get("base64"), r.get("mimetype")
    except Exception:
        log.exception("Não consegui baixar a mídia %s", message_id)
        return None, None


# Gerência da instância (usada pela tela /painel/whatsapp)

async def criar_instancia() -> None:
    try:
        await _post(
            "/instance/create",
            {"instanceName": settings.evolution_instance, "integration": "WHATSAPP-BAILEYS", "qrcode": True},
        )
    except httpx.HTTPStatusError:
        pass  # já existe
    except Exception:
        log.warning("Evolution API indisponível ao criar a instância")


async def garantir_instancia(tentativas: int = 60, espera: float = 3.0) -> bool:
    """Na subida do app: cria a instância padrão na Evolution (espera ela ficar de pé). Falta só ler o QR code.

    Devolve True se a instância existe no fim (criada agora ou já existente).
    """
    if not settings.evolution_api_key or not settings.evolution_instance:
        return False
    for _ in range(tentativas):
        try:
            await _post(
                "/instance/create",
                {"instanceName": settings.evolution_instance, "integration": "WHATSAPP-BAILEYS", "qrcode": True},
            )
            log.info("Instância '%s' criada na Evolution: leia o QR code em /painel/whatsapp", settings.evolution_instance)
            return True
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (400, 403, 409):  # já existe
                log.info("Instância '%s' já existe na Evolution", settings.evolution_instance)
                return True
            log.warning("Evolution respondeu %s ao criar a instância", e.response.status_code)
        except Exception:
            pass  # Evolution ainda subindo: tenta de novo
        await asyncio.sleep(espera)
    log.warning("Não consegui criar a instância '%s': Evolution fora do ar? (use /painel/whatsapp depois)",
                settings.evolution_instance)
    return False


async def estado_instancia() -> str:
    """open | connecting | close | indisponível (instância não existe ou Evolution fora do ar)."""
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(_url(f"/instance/connectionState/{settings.evolution_instance}"), headers=_headers())
        if r.status_code == 404:
            return "indisponível"
        r.raise_for_status()
        return (r.json().get("instance") or {}).get("state", "desconhecido")
    except Exception:
        return "indisponível"


async def qrcode_instancia() -> str | None:
    try:
        r = await _get(f"/instance/connect/{settings.evolution_instance}")
        return r.get("base64")
    except Exception:
        return None


# ---------- Entrada: webhook -> mensagem padrão ----------

def _numero(jid: str | None) -> str:
    return (jid or "").split("@")[0].split(":")[0]


def _vale_mensagem_do_proprio_numero(body: dict, data: dict, key: dict) -> bool:
    """Mensagem que saiu do número conectado: só vale na conversa com você mesmo ("Mensagem para você"), com a opção
    ligada, e nunca se foi o próprio bot que enviou (senão ele responderia a si mesmo em laço)."""
    if not settings.proprio_numero_ativo:
        return False
    if _envios_em_andamento or key.get("id") in _ids_enviados:
        return False
    msg = data.get("message") or {}
    texto = msg.get("conversation") or (msg.get("extendedTextMessage") or {}).get("text") or ""
    if texto.strip() and texto.strip() in _textos_enviados:
        return False
    dono = _numero(body.get("sender"))
    chat = {_numero(key.get("remoteJid")), _numero(key.get("senderPn")), _numero(key.get("remoteJidAlt"))} - {""}
    return bool(dono) and dono in chat  # conversa com outra pessoa nunca entra

def converter(body: dict) -> MensagemEntrada | None:
    """Converte o evento messages.upsert. Devolve None para ruído (fromMe, grupo, status, tipo sem suporte)."""
    evento = str(body.get("event", "")).lower().replace("_", ".")
    if evento != "messages.upsert":
        return None
    data = body.get("data") or {}
    if isinstance(data, list):
        data = data[0] if data else {}
    key = data.get("key") or {}
    jid = key.get("remoteJid", "")
    if jid.endswith("@g.us") or jid == "status@broadcast" or "@newsletter" in jid:
        return None
    if key.get("fromMe") and not _vale_mensagem_do_proprio_numero(body, data, key):
        return None
    if jid.endswith("@lid"):
        jid = key.get("senderPn") or key.get("remoteJidAlt") or jid
    numero = jid.split("@")[0]
    msg = data.get("message") or {}
    b64 = msg.get("base64") or data.get("base64")

    base = dict(canal="whatsapp", cliente_id=numero, message_id=key.get("id", ""), nome=data.get("pushName"))
    if "conversation" in msg:
        return MensagemEntrada(**base, tipo="texto", conteudo=msg["conversation"])
    if "extendedTextMessage" in msg:
        return MensagemEntrada(**base, tipo="texto", conteudo=msg["extendedTextMessage"].get("text", ""))
    if "audioMessage" in msg:
        return MensagemEntrada(**base, tipo="audio", midia_b64=b64,
                               midia_mime=msg["audioMessage"].get("mimetype", "audio/ogg"))
    if "imageMessage" in msg:
        im = msg["imageMessage"]
        return MensagemEntrada(**base, tipo="imagem", conteudo=im.get("caption", ""), midia_b64=b64,
                               midia_mime=im.get("mimetype", "image/jpeg"))
    # Documento, vídeo, figurinha, localização, contato: não dá para ler, mas o cliente recebe resposta
    for chave in ("documentMessage", "documentWithCaptionMessage", "videoMessage", "stickerMessage",
                  "locationMessage", "contactMessage", "contactsArrayMessage"):
        if chave in msg:
            interno = msg[chave] or {}
            legenda = interno.get("caption") or (interno.get("message", {}).get("documentMessage", {}) or {}).get("caption", "")
            return MensagemEntrada(**base, tipo="outro", conteudo=legenda or "", midia_mime=chave)
    return None


# ---------- Etapa 02, passo 4: agrupar mensagens ----------

class Agrupador:
    """Junta mensagens do mesmo cliente que chegam em sequência. Fecha após N segundos sem mensagem nova."""

    def __init__(self, espera: float, ao_fechar: Callable[[list[MensagemEntrada]], Awaitable[None]]):
        self.espera = espera
        self.ao_fechar = ao_fechar
        self.buffer: dict[str, list[MensagemEntrada]] = {}
        self.tarefas: dict[str, asyncio.Task] = {}

    def descartar(self, chave: str) -> None:
        """Joga fora o que estava esperando (usado no #reset e no #parar)."""
        if t := self.tarefas.pop(chave, None):
            t.cancel()
        self.buffer.pop(chave, None)

    def adicionar(self, msg: MensagemEntrada) -> None:
        chave = msg.cliente_id
        self.buffer.setdefault(chave, []).append(msg)
        if t := self.tarefas.get(chave):
            t.cancel()
        self.tarefas[chave] = asyncio.create_task(self._esperar(chave))

    async def _esperar(self, chave: str) -> None:
        try:
            await asyncio.sleep(self.espera)
        except asyncio.CancelledError:
            return
        self.tarefas.pop(chave, None)
        msgs = self.buffer.pop(chave, [])
        if msgs:
            try:
                await self.ao_fechar(msgs)
            except Exception:
                log.exception("Erro ao processar lote do WhatsApp")


async def entregar(numero: str, res: Resultado) -> None:
    """Etapa 07 (parte 2): envia pelo WhatsApp."""
    from app.pipeline.resposta import tempo_digitando

    if res.tipo_resposta == "audio" and res.audio_path:
        await enviar_audio(numero, res.audio_path)
    for t in res.textos:  # um balão por vez, com "digitando…" proporcional ao tamanho
        await enviar_texto(numero, t, tempo_digitando(t))


async def _processar_lote(msgs: list[MensagemEntrada]) -> None:
    from app.pipeline import orquestrador

    from app.pipeline.midia import tipo_suportado

    for m in msgs:
        if m.tipo in ("audio", "imagem") and not m.midia_b64:
            m.midia_b64, mime = await baixar_midia(m.message_id)
            m.midia_mime = mime or m.midia_mime
        if m.tipo in ("audio", "imagem") and m.midia_b64:
            # formato que a OpenAI não lê (ex.: HEIC do iPhone): vira "outro", com resposta amigável
            tipo, _ = tipo_suportado(m.midia_mime, len(m.midia_b64) * 3 // 4)
            if tipo == "outro":
                m.tipo, m.midia_b64 = "outro", None
    res = await run_in_threadpool(orquestrador.processar, msgs)
    if res.tipo_resposta != "nenhuma":
        await entregar(msgs[0].cliente_id, res)


agrupador = Agrupador(settings.agrupar_segundos, _processar_lote)


@router.post("/webhook/evolution")
async def webhook(request: Request):
    """Responde 200 na hora; o processamento segue em segundo plano."""
    from app.pipeline import orquestrador

    body = await request.json()
    msg = converter(body)
    if not msg:
        return {"ok": True, "ignorado": "ruido"}
    confirmacao = await run_in_threadpool(orquestrador.executar_comando, msg)
    if confirmacao is not None:
        agrupador.descartar(msg.cliente_id)
        if confirmacao:
            try:
                await enviar_texto(msg.cliente_id, confirmacao)
            except Exception:
                log.exception("Não consegui confirmar o comando")
        return {"ok": True, "comando": True}
    segue, motivo = await run_in_threadpool(orquestrador.aceitar, msg)
    if not segue:
        return {"ok": True, "ignorado": motivo}
    agrupador.adicionar(msg)
    return {"ok": True}
