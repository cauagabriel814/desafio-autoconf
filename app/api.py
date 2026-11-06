"""API usada pelas telas: leads, exportação e CRUD de carros."""

from typing import Literal

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app import exportar as exp
from app.db import repo
from app.db.seed import importar_csv

router = APIRouter()


# ---------- Leads ----------

@router.get("/leads")
def leads(status: str | None = None, urgencia: str | None = None):
    return repo.listar_leads(status or None, urgencia or None)


@router.get("/leads/{lead_id}")
def lead(lead_id: int):
    det = repo.detalhe_lead(lead_id)
    if not det:
        raise HTTPException(404, "Lead não encontrado")
    return det


@router.post("/leads/{lead_id}/resumo")
def resumo(lead_id: int):
    """Resumo da conversa para o vendedor, a qualquer momento (não só na transferência)."""
    from app import llm
    from app.agente.tools import gerar_resumo

    lead = repo.obter_lead(lead_id)
    if not lead:
        raise HTTPException(404, "Lead não encontrado")
    transferencias = (repo.detalhe_lead(lead_id) or {}).get("transferencias") or []
    if transferencias:  # já foi para o vendedor: o resumo sempre diz por quê
        situacao = transferencias[-1]["motivo"]
    else:
        situacao = "atendimento em andamento com a assistente (ainda não transferido)" if lead.status == "bot" \
            else f"status: {lead.status}"
    try:
        texto = gerar_resumo(lead_id, situacao)
    except llm.SemChaveOpenAI as e:
        raise HTTPException(503, str(e))
    repo.atualizar_ficha(lead_id, {"resumo_conversa": texto})
    return {"resumo": texto}


@router.post("/leads/{lead_id}/devolver")
def devolver(lead_id: int):
    if not repo.obter_lead(lead_id):
        raise HTTPException(404, "Lead não encontrado")
    repo.definir_status(lead_id, "bot")
    return {"ok": True}


@router.get("/export")
def export(
    formato: Literal["csv", "json", "hubspot", "pipedrive", "salesforce"] = "csv",
    status: str | None = None,
    lead_id: int | None = None,
):
    # Uma consulta só (sem mensagens). O lead escolhido na ficha sempre sai; em lote, só os válidos.
    detalhes = repo.leads_para_exportar(status or None, [lead_id] if lead_id else None)
    if not lead_id:
        detalhes = [d for d in detalhes if exp.valido(d)]
    conteudo, media, nome = exp.exportar(detalhes, formato)
    return Response(conteudo, media_type=media, headers={"Content-Disposition": f'attachment; filename="{nome}"'})


# ---------- Configuração do agente ----------

@router.get("/config")
def ver_config():
    """Tudo o que o agente usa: o que dá para mudar (editaveis) e o que é só consulta (modelos, libs, serviços)."""
    from app import configuracao

    return configuracao.atual()


@router.put("/config")
def mudar_config(dados: dict):
    """Muda um ou mais ajustes. Ex.: {"modo_resposta": "sempre_texto", "nome_atendente": "Bia"}. Vale na hora."""
    from app import configuracao

    try:
        return configuracao.salvar(dados)
    except configuracao.ValorInvalido as e:
        raise HTTPException(400, str(e))


@router.post("/config/restaurar")
def restaurar_config():
    """Volta todos os ajustes para o que está no .env."""
    from app import configuracao

    return configuracao.restaurar()


# ---------- Carros ----------

class CarroIn(BaseModel):
    marca: str = Field(min_length=1)
    modelo: str = Field(min_length=1)
    ano: int = Field(ge=1950, le=2100)
    km: int = Field(ge=0)
    preco: int = Field(ge=1000, description="Em reais inteiros (55 mil = 55000)")
    tipo: str = Field(min_length=1)
    disponivel: bool = True


@router.get("/carros")
def carros():
    return repo.listar_carros()


@router.post("/carros", status_code=201)
def criar_carro(c: CarroIn):
    return repo.salvar_carro(c.model_dump())


@router.put("/carros/{carro_id}")
def editar_carro(carro_id: int, c: CarroIn):
    try:
        return repo.salvar_carro(c.model_dump(), carro_id)
    except KeyError:
        raise HTTPException(404, "Carro não encontrado")


@router.delete("/carros/{carro_id}")
def excluir_carro(carro_id: int):
    if not repo.excluir_carro(carro_id):
        raise HTTPException(404, "Carro não encontrado")
    return {"ok": True}


@router.get("/carros/modelo.csv")
def modelo_csv():
    conteudo = "marca,modelo,ano,km,preco,tipo\nVW,Saveiro,2015,190000,55000,Picape\nFiat,Argo Drive 1.0,2023,22000,69900,Hatch\n"
    return Response(conteudo, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="modelo_estoque.csv"'})


@router.post("/carros/importar")
async def importar(arquivo: UploadFile = File(...)):
    try:
        n = importar_csv((await arquivo.read()).decode("utf-8-sig"))
    except (KeyError, ValueError) as e:
        raise HTTPException(400, f"CSV inválido. Colunas esperadas: marca,modelo,ano,km,preco,tipo ({e})")
    return {"importados": n}
