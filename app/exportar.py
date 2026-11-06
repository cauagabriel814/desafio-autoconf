"""T4: exportação de leads. CSV/JSON genéricos e mapeamento de campos para CRMs."""

import csv
import io
import json

# Ficha plana usada no CSV/JSON genérico
CAMPOS = [
    "cliente_id", "telefone", "nome", "titulo", "canal", "status", "tipo_interesse", "modelos_interesse", "preco_min", "preco_max",
    "troca_tem", "troca_modelo", "troca_ano", "quer_financiar", "entrada", "urgencia", "primeiro_carro",
    "idade", "faixa_renda", "sentimento", "motivo_transferencia", "resumo", "proximo_passo",
]

# campo nosso -> coluna esperada pelo importador de cada CRM
MAPEAMENTOS: dict[str, dict[str, str]] = {
    "hubspot": {
        "nome": "First Name",
        "telefone": "Phone Number",
        "canal": "Lead Source",
        "status": "Lead Status",
        "tipo_interesse": "Vehicle Type",
        "modelos_interesse": "Vehicle Interest",
        "preco_max": "Budget",
        "troca_modelo": "Trade-in Vehicle",
        "quer_financiar": "Wants Financing",
        "urgencia": "Urgency",
        "resumo": "Notes",
        "proximo_passo": "Next Step",
    },
    "pipedrive": {
        "nome": "Person - Name",
        "telefone": "Person - Phone",
        "titulo": "Deal - Title",
        "preco_max": "Deal - Value",
        "canal": "Deal - Source",
        "troca_modelo": "Deal - Trade-in",
        "quer_financiar": "Deal - Financing",
        "urgencia": "Deal - Urgency",
        "resumo": "Note - Content",
        "proximo_passo": "Activity - Subject",
    },
    "salesforce": {
        "nome": "LastName",
        "telefone": "Phone",
        "titulo": "Title",
        "canal": "LeadSource",
        "status": "Status",
        "modelos_interesse": "Product_Interest__c",
        "preco_max": "Budget__c",
        "troca_modelo": "Trade_In__c",
        "quer_financiar": "Financing__c",
        "urgencia": "Rating",
        "resumo": "Description",
        "proximo_passo": "Next_Step__c",
    },
}

FORMATOS = ["csv", "json", "hubspot", "pipedrive", "salesforce"]


CANAL_ROTULO = {"whatsapp": "WhatsApp", "web": "Site", "terminal": "Simulador"}
_DADOS_DE_LEAD = ("tipo_interesse", "modelos_interesse", "uso", "preco_max", "preco_min", "troca_tem",
                  "quer_financiar", "entrada", "urgencia")


def valido(lead: dict) -> bool:
    """Lead que vale a pena ir para um CRM: cliente real (WhatsApp ou site), não arquivado e com informação."""
    if lead["canal"] not in ("whatsapp", "web") or lead["status"] == "arquivado":
        return False
    ficha = lead.get("ficha") or {}
    return bool(lead.get("transferencias")) or any(ficha.get(k) not in (None, "", []) for k in _DADOS_DE_LEAD)


def telefone(lead: dict) -> str:
    """Só o WhatsApp tem telefone de verdade. Formato +55 11 99999-0000."""
    if lead["canal"] != "whatsapp":
        return ""
    d = "".join(c for c in lead["cliente_id"].split("~")[0] if c.isdigit())
    if len(d) >= 12 and d.startswith("55"):
        return f"+{d[:2]} {d[2:4]} {d[4:-4]}-{d[-4:]}"
    return f"+{d}" if d else ""


def titulo_negocio(ficha: dict) -> str:
    partes = [ficha.get("tipo_interesse"), ", ".join(ficha.get("modelos_interesse") or []) or None]
    if ficha.get("preco_max"):
        partes.append(f"até R$ {ficha['preco_max']:,}".replace(",", "."))
    return " · ".join(p for p in partes if p) or "Interesse em carro seminovo"


def ficha_plana(lead: dict) -> dict:
    ficha = lead.get("ficha") or {}
    transf = (lead.get("transferencias") or [{}])[-1] if lead.get("transferencias") else {}
    linha = {k: ficha.get(k) for k in CAMPOS}
    tel = telefone(lead)
    nome = lead.get("nome") or ficha.get("nome")
    if not nome:
        nome = f"Lead WhatsApp {tel}" if tel else f"Lead site {lead['id']}"
    linha.update(
        cliente_id=lead["cliente_id"].split("~")[0],
        telefone=tel,
        nome=nome,
        canal=CANAL_ROTULO.get(lead["canal"], lead["canal"]),
        status={"humano": "com vendedor", "bot": "em atendimento", "parado": "parado"}.get(lead["status"], lead["status"]),
        motivo_transferencia=(transf.get("motivo") or "").replace("_", " ") or None,
        resumo=transf.get("resumo"),
        titulo=titulo_negocio(ficha),
    )
    if isinstance(linha.get("modelos_interesse"), list):
        linha["modelos_interesse"] = ", ".join(linha["modelos_interesse"])
    return linha


def _valor(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "sim" if v else "não"
    return str(v)


def exportar(leads: list[dict], formato: str) -> tuple[str, str, str]:
    """Devolve (conteúdo, media type, nome do arquivo)."""
    linhas = [ficha_plana(l) for l in leads]
    if formato == "json":
        return json.dumps(linhas, ensure_ascii=False, indent=2, default=str), "application/json", "leads.json"
    if formato in MAPEAMENTOS:
        mapa = MAPEAMENTOS[formato]
        colunas = list(mapa.values())
        linhas = [{mapa[k]: _valor(l.get(k)) for k in mapa} for l in linhas]
        nome = f"leads_{formato}.csv"
    else:
        colunas = CAMPOS
        linhas = [{k: _valor(l.get(k)) for k in CAMPOS} for l in linhas]
        nome = "leads.csv"
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=colunas)
    w.writeheader()
    w.writerows(linhas)
    return buf.getvalue(), "text/csv; charset=utf-8", nome
