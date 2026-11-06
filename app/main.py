import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import api, telas
from app.canais import evolution, web
from app.config import settings
from app.db.seed import iniciar_banco
from app.telas import rodada

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    iniciar_banco()
    from app import configuracao

    configuracao.carregar()  # ajustes salvos pela tela /painel/config valem por cima do .env
    if not settings.openai_api_key:
        logging.warning("OPENAI_API_KEY não configurada: o agente vai responder com aviso de configuração.")
    yield


app = FastAPI(title="Agente de pré-venda Autoconf", lifespan=lifespan)

app.include_router(evolution.router, tags=["canal whatsapp"])
app.include_router(web.router, tags=["canal web"])
app.include_router(api.router, tags=["api das telas"])
app.include_router(telas.router, include_in_schema=False)
app.include_router(rodada.router, tags=["testes do agente"])

app.mount("/static", StaticFiles(directory=Path(__file__).parent / "telas" / "static"), name="static")
app.mount("/audios", StaticFiles(directory=settings.audios_dir), name="audios")


@app.get("/saude", tags=["infra"])
def saude():
    return {"ok": True, "openai": bool(settings.openai_api_key), "langfuse": settings.langfuse_ativo}
