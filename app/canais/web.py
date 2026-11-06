"""Canal da tela de chat (T1): POST /chat recebe e já devolve a resposta."""

import base64
import uuid

from fastapi import APIRouter, File, Form, UploadFile
from starlette.concurrency import run_in_threadpool

from app.canais.payload import MensagemEntrada
from app.db import repo
from app.pipeline import orquestrador

router = APIRouter()


@router.post("/chat")
async def chat(
    cliente_id: str = Form(...),
    texto: str = Form(""),
    nome: str | None = Form(None),
    arquivo: UploadFile | None = File(None),
):
    tipo, b64, mime = "texto", None, None
    if arquivo is not None and arquivo.filename:
        from app.pipeline.midia import tipo_suportado

        dados = await arquivo.read()
        mime = arquivo.content_type or ""
        tipo, _ = tipo_suportado(mime, len(dados)) if dados else ("outro", "arquivo vazio")
        b64 = base64.b64encode(dados).decode() if tipo != "outro" else None
    if tipo == "texto" and not texto.strip():
        return {"textos": [], "ignorado": "vazia"}

    msg = MensagemEntrada(
        canal="web", cliente_id=cliente_id, message_id=f"web-{uuid.uuid4().hex}", tipo=tipo,
        conteudo=texto, midia_b64=b64, midia_mime=mime, nome=nome,
    )
    # #reset/#parar não valem na tela (são comandos de teste do WhatsApp)
    segue, motivo = await run_in_threadpool(orquestrador.aceitar, msg)
    if not segue:
        lead = repo.obter_ou_criar_lead("web", cliente_id)
        return {"textos": [], "ignorado": motivo, "status_lead": lead.status}
    res = await run_in_threadpool(orquestrador.processar, [msg])
    return {
        "textos": res.textos,
        "audio_url": f"/audios/{res.audio_path}" if res.audio_path else None,
        "transferido": res.transferido,
        "status_lead": res.status_lead,
        "ignorado": res.ignorado,
        "texto_cliente": res.texto_cliente,
    }


@router.get("/chat/{cliente_id}/historico")
def historico(cliente_id: str):
    lead = repo.obter_ou_criar_lead("web", cliente_id)
    det = repo.detalhe_lead(lead.id)
    return {
        "status_lead": det["status"],
        "mensagens": [
            {
                "papel": m["papel"],
                "texto": m["texto"],
                "audio_url": f"/audios/{m['audio_path']}" if m["audio_path"] else None,
            }
            for m in det["mensagens"]
            if m["status"] != "bloqueada"
        ],
    }
