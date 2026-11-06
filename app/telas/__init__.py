"""Telas HTML (T1 a T5 + conexão do WhatsApp)."""

import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.templating import Jinja2Templates

from app.canais import evolution
from app.catalogo import catalogo_com_estoque
from app.config import settings
from app.db import repo
from app.exportar import CAMPOS, FORMATOS, MAPEAMENTOS

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


_CSS = Path(__file__).parent / "static" / "estilo.css"
# Muda quando o CSS muda: o navegador não usa a versão velha do cache
VERSAO_CSS = str(int(_CSS.stat().st_mtime)) if _CSS.exists() else "1"


def _ctx(request: Request, **kw) -> dict:
    return {"loja": settings.nome_loja, "atendente": settings.nome_atendente, "versao_css": VERSAO_CSS, **kw}


@router.get("/")
def inicio(request: Request):
    return templates.TemplateResponse(request,"inicio.html", _ctx(request))


@router.get("/chat")
def tela_chat(request: Request):
    return templates.TemplateResponse(request,"chat.html", _ctx(request))


POR_PAGINA = 10
CANAIS = {"": ("whatsapp", "web"), "whatsapp": ("whatsapp",), "web": ("web",), "terminal": ("terminal",),
          "todos": ("whatsapp", "web", "terminal")}


@router.get("/painel")
def tela_painel(request: Request, status: str = "", urgencia: str = "", canal: str = "", pagina: int = 1):
    canal = canal if canal in CANAIS else ""
    # Filtro, ordem e paginação no banco: só os 10 da página saem do SQLite
    leads, total = repo.pagina_leads(status or None, urgencia or None, CANAIS[canal], pagina, POR_PAGINA)
    paginas = max(1, -(-total // POR_PAGINA))
    pagina = min(max(1, pagina), paginas)
    inicio = (pagina - 1) * POR_PAGINA
    return templates.TemplateResponse(request, "painel.html", _ctx(
        request, leads=leads, status=status, urgencia=urgencia, canal=canal,
        pagina=pagina, paginas=paginas, total=total, inicio=inicio,
    ))


@router.get("/painel/lead/{lead_id}")
def tela_lead(request: Request, lead_id: int):
    det = repo.detalhe_lead(lead_id)
    if not det:
        raise HTTPException(404, "Lead não encontrado")
    from app.agente.ficha import situacao_campos

    return templates.TemplateResponse(request, "lead.html", _ctx(
        request, lead=det, campos=situacao_campos(det["ficha"])))


@router.get("/painel/exportar")
def tela_exportar(request: Request, formato: str = "hubspot"):
    from app.exportar import valido

    detalhes = repo.leads_para_exportar()  # uma consulta, sem carregar as mensagens
    validos = [d for d in detalhes if valido(d)]
    total = len(validos)
    humano = sum(1 for d in validos if d["status"] == "humano")
    fora = len(detalhes) - total
    formato = formato if formato in FORMATOS else "hubspot"
    return templates.TemplateResponse(request, "exportar.html", _ctx(
        request, formatos=FORMATOS, mapeamentos=MAPEAMENTOS, campos=CAMPOS, formato=formato,
        total=total, humano=humano, fora=fora,
    ))


@router.get("/painel/estoque")
def tela_estoque(request: Request):
    carros = repo.listar_carros()
    return templates.TemplateResponse(request, "estoque.html", _ctx(
        request, carros=carros, catalogo=catalogo_com_estoque(carros), ano_atual=datetime.now().year,
    ))


@router.get("/painel/config")
def tela_config(request: Request):
    from app import configuracao

    return templates.TemplateResponse(request, "config.html", _ctx(request, cfg=configuracao.atual()))


RELATORIO_JSON = Path("relatorios") / "resultado.json"
PARCIAL_JSON = Path("relatorios") / "resultado_parcial.json"
# Os 6 casos da tabela do PDF (o 6 tem duas variações: dólar e áudio com ruído)
CASOS_PDF = ("1-", "2-", "3-", "4-", "5-", "6a-", "6b-")


def _ler_json(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    except (OSError, ValueError):
        return None


AO_VIVO_JSON = Path("relatorios") / "ao_vivo.json"


@router.get("/painel/testes/ao-vivo")
def testes_ao_vivo(request: Request):
    """Pedaço de HTML com os casos já terminados da rodada em andamento (a tela busca a cada poucos segundos)."""
    casos = (_ler_json(AO_VIVO_JSON) or {}).get("casos", [])
    return templates.TemplateResponse(request, "_ao_vivo.html", _ctx(
        request, casos=casos, passaram=sum(1 for c in casos if c["ok"]), casos_pdf=CASOS_PDF))


@router.get("/painel/testes")
def tela_testes(request: Request, filtro: str = "", rodada: str = ""):
    from app.telas.rodada import GRUPOS, ids_do_grupo

    parcial = _ler_json(PARCIAL_JSON)
    rodada = "parcial" if rodada == "parcial" and parcial else "completa"
    dados = parcial if rodada == "parcial" else _ler_json(RELATORIO_JSON)
    casos = (dados or {}).get("casos", [])
    if filtro == "falhas":
        casos = [c for c in casos if not c["ok"]]
    elif filtro == "pdf":
        casos = [c for c in casos if c["id"].startswith(CASOS_PDF)]
    n_pdf = sum(1 for c in (dados or {}).get("casos", []) if c["id"].startswith(CASOS_PDF))
    return templates.TemplateResponse(request, "testes.html", _ctx(
        request, dados=dados, casos=casos, filtro=filtro, rodada=rodada, tem_parcial=bool(parcial),
        grupos=GRUPOS, n_pdf=n_pdf, casos_pdf=CASOS_PDF, contagem={g: len(ids_do_grupo(g)) for g in GRUPOS}))


@router.get("/painel/whatsapp")
async def tela_whatsapp(request: Request):
    # Consulta o estado primeiro; só cria a instância se ela não existir e só busca o QR se precisar
    estado = await evolution.estado_instancia()
    if estado == "indisponível":
        await evolution.criar_instancia()
        estado = await evolution.estado_instancia()
    qr = await evolution.qrcode_instancia() if estado not in ("open", "indisponível") else None
    return templates.TemplateResponse(request,
        "whatsapp.html",
        _ctx(request, estado=estado, qr=qr, instancia=settings.evolution_instance, gatilho=settings.trigger_keyword),
    )
