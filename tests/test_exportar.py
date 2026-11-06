import csv
import io
import json

from app.db import repo
from app.exportar import exportar


def _lead():
    lead = repo.obter_ou_criar_lead("whatsapp", "5511977776666", "Marcos")
    repo.atualizar_ficha(lead.id, {"tipo_interesse": "SUV", "preco_max": 120000, "troca_tem": True,
                                   "troca_modelo": "Gol", "troca_ano": 2018, "quer_financiar": True,
                                   "modelos_interesse": ["Creta Limited"]})
    return repo.detalhe_lead(lead.id)


def test_csv_generico():
    conteudo, _, nome = exportar([_lead()], "csv")
    linha = next(csv.DictReader(io.StringIO(conteudo)))
    assert nome == "leads.csv"
    assert linha["preco_max"] == "120000" and linha["quer_financiar"] == "sim" and linha["canal"] == "WhatsApp"


def test_json():
    conteudo, media, _ = exportar([_lead()], "json")
    assert media == "application/json"
    assert json.loads(conteudo)[0]["troca_modelo"] == "Gol"


def test_hubspot_usa_colunas_do_crm():
    conteudo, _, nome = exportar([_lead()], "hubspot")
    linha = next(csv.DictReader(io.StringIO(conteudo)))
    assert nome == "leads_hubspot.csv"
    assert linha["Phone Number"] == "+55 11 97777-6666" and linha["Budget"] == "120000"
    assert linha["First Name"] == "Marcos"


def test_pipedrive_titulo_descritivo_e_nome_obrigatorio():
    lead = repo.obter_ou_criar_lead("whatsapp", "5521988881234")  # sem nome
    repo.atualizar_ficha(lead.id, {"tipo_interesse": "Hatch", "modelos_interesse": ["Polo"], "preco_max": 80000})
    conteudo, _, _ = exportar([repo.detalhe_lead(lead.id)], "pipedrive")
    linha = next(csv.DictReader(io.StringIO(conteudo)))
    assert linha["Deal - Title"] == "Hatch · Polo · até R$ 80.000"
    assert linha["Person - Name"] == "Lead WhatsApp +55 21 98888-1234"
    assert linha["Deal - Source"] == "WhatsApp"


def test_lead_do_site_sai_sem_telefone_falso():
    lead = repo.obter_ou_criar_lead("web", "web-abc123")
    repo.atualizar_ficha(lead.id, {"tipo_interesse": "SUV"})
    conteudo, _, _ = exportar([repo.detalhe_lead(lead.id)], "salesforce")
    linha = next(csv.DictReader(io.StringIO(conteudo)))
    assert linha["Phone"] == "" and linha["LastName"].startswith("Lead site") and linha["LeadSource"] == "Site"


# ---------- quais leads são válidos para exportar ----------

def test_validos():
    from app.exportar import valido

    def det(canal, cid, ficha=None, status="bot"):
        l = repo.obter_ou_criar_lead(canal, cid)
        if ficha:
            repo.atualizar_ficha(l.id, ficha)
        if status != "bot":
            repo.definir_status(l.id, status)
        return repo.detalhe_lead(l.id)

    assert valido(det("whatsapp", "5511900000101", {"preco_max": 90000}))
    assert valido(det("web", "web-valido", {"tipo_interesse": "SUV"}))
    assert not valido(det("terminal", "sim-x1-abcd", {"preco_max": 90000}))  # simulador
    assert not valido(det("whatsapp", "5511900000102"))  # só disse "oi"
    assert not valido(det("whatsapp", "5511900000103", {"preco_max": 90000}, status="arquivado"))  # #reset
    assert not valido(det("web", "web-so-nome", {"nome": "Ana"}))  # sem informação de compra
