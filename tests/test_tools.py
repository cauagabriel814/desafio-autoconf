"""Testes diretos das 4 tools do agente (sem chamar o modelo)."""

import json
import uuid

import pytest

from app.agente import tools as tools_mod
from app.db import repo


def _novo_lead():
    return repo.obter_ou_criar_lead("terminal", f"tool-{uuid.uuid4().hex[:6]}")


def _tools(lead_id):
    return {t.name: t for t in tools_mod.criar_tools(lead_id)}


@pytest.fixture
def lead():
    return _novo_lead()


@pytest.fixture
def t(lead):
    return _tools(lead.id)


# ---------- buscar_estoque ----------

def test_buscar_estoque_com_filtros(t):
    r = json.loads(t["buscar_estoque"].invoke({"tipo": "SUV", "preco_max": 120000}))
    assert {c["modelo"] for c in r["encontrados"]} == {
        "Creta Limited", "T-Cross Comfortline", "Kicks SV", "Duster Iconic"
    }
    assert all(c["preco"] <= 120000 and c["tipo"] == "SUV" for c in r["encontrados"])


def test_buscar_estoque_sem_filtro_devolve_tudo(t):
    r = json.loads(t["buscar_estoque"].invoke({}))
    assert len(r["encontrados"]) == 20


def test_buscar_estoque_por_modelo_e_ano(t):
    r = json.loads(t["buscar_estoque"].invoke({"marca_ou_modelo": "Corolla", "ano_min": 2022}))
    assert [c["modelo"] for c in r["encontrados"]] == ["Corolla Cross XRE"]


def test_buscar_estoque_sem_resultado_devolve_estoque_completo(t):
    r = json.loads(t["buscar_estoque"].invoke({"marca_ou_modelo": "Hilux"}))
    assert r["encontrados"] == []
    assert "aviso" in r and len(r["estoque_completo"]) == 20


def test_buscar_estoque_tipo_invalido_e_rejeitado(t):
    with pytest.raises(Exception):
        t["buscar_estoque"].invoke({"tipo": "Moto"})


# ---------- detalhes_carro ----------

def test_detalhes_carro_existente(t):
    civic = repo.buscar_carros(texto="civic")[0]
    r = json.loads(t["detalhes_carro"].invoke({"carro_id": civic["id"]}))
    assert r["modelo"] == "Civic Touring" and r["preco"] == 132900 and r["km"] == 61000


def test_detalhes_carro_inexistente(t):
    r = json.loads(t["detalhes_carro"].invoke({"carro_id": 99999}))
    assert r == {"erro": "Carro não encontrado"}


# ---------- transferir_humano ----------

@pytest.fixture
def resumo_fixo(monkeypatch):
    monkeypatch.setattr(tools_mod, "gerar_resumo", lambda lead_id, motivo, callbacks=None: f"resumo de teste ({motivo})")


def test_transferir_humano_muda_status_e_grava(t, lead, resumo_fixo):
    r = json.loads(t["transferir_humano"].invoke({"motivo": "negociacao", "urgencia": "media"}))
    assert r["ok"] is True
    det = repo.detalhe_lead(lead.id)
    assert det["status"] == "humano"
    assert det["ficha"]["urgencia"] == "media"
    assert len(det["transferencias"]) == 1
    tr = det["transferencias"][0]
    assert tr["motivo"] == "negociacao" and tr["resumo"] == "resumo de teste (negociacao)"


def test_transferir_nao_sobrescreve_urgencia_do_cliente(t, lead, resumo_fixo):
    repo.atualizar_ficha(lead.id, {"urgencia": "baixa"})
    t["transferir_humano"].invoke({"motivo": "negociacao", "urgencia": "media"})
    det = repo.detalhe_lead(lead.id)
    assert det["ficha"]["urgencia"] == "baixa" and det["transferencias"][0]["urgencia"] == "baixa"


def test_transferir_por_urgencia_e_sempre_alta(t, lead, resumo_fixo):
    t["transferir_humano"].invoke({"motivo": "urgencia", "urgencia": "media"})
    assert repo.detalhe_lead(lead.id)["ficha"]["urgencia"] == "alta"


def test_transferir_humano_nao_duplica(t, lead, resumo_fixo):
    t["transferir_humano"].invoke({"motivo": "reclamacao", "urgencia": "alta"})
    r = json.loads(t["transferir_humano"].invoke({"motivo": "reclamacao", "urgencia": "alta"}))
    assert "aviso" in r
    assert len(repo.detalhe_lead(lead.id)["transferencias"]) == 1


def test_transferir_qualificado_com_checklist_completo(t, lead, resumo_fixo):
    repo.atualizar_ficha(lead.id, {"tipo_interesse": "SUV", "preco_max": 120000, "quer_financiar": False,
                                   "troca_tem": False, "urgencia": "baixa", "visita": "sábado de manhã"})
    t["transferir_humano"].invoke({"motivo": "qualificado", "urgencia": "baixa"})
    det = repo.detalhe_lead(lead.id)
    assert det["status"] == "humano"
    assert det["transferencias"][0]["motivo"] == "qualificado" and det["transferencias"][0]["resumo"]


def test_transferir_qualificado_recusa_sem_valor_mesmo_com_nao_sabe(t, lead, resumo_fixo):
    # cliente "não soube" o valor: nada pendente, mas sem a resposta não qualifica
    repo.atualizar_ficha(lead.id, {"tipo_interesse": "SUV", "quer_financiar": False, "nao_informado": ["faixa_preco"]})
    r = json.loads(t["transferir_humano"].invoke({"motivo": "qualificado"}))
    assert r["ok"] is False and repo.detalhe_lead(lead.id)["status"] == "bot"


def test_transferir_qualificado_recusa_com_itens_faltando(t, lead, resumo_fixo):
    repo.atualizar_ficha(lead.id, {"tipo_interesse": "SUV", "preco_max": 120000})
    r = json.loads(t["transferir_humano"].invoke({"motivo": "qualificado"}))
    assert r["ok"] is False and "pagamento" in r["erro"]
    det = repo.detalhe_lead(lead.id)
    assert det["status"] == "bot" and det["transferencias"] == []


def test_buscar_estoque_em_ordem_de_preco(t):
    r = json.loads(t["buscar_estoque"].invoke({"preco_max": 110000}))
    precos = [c["preco"] for c in r["encontrados"]]
    assert precos == sorted(precos) and precos[-1] == 98900


def test_pediu_humano_sem_o_cliente_pedir_e_recusado(t, lead, resumo_fixo):
    repo.salvar_mensagem(lead.id, "cliente", "Você é um robô?")
    r = json.loads(t["transferir_humano"].invoke({"motivo": "pediu_humano"}))
    assert r["ok"] is False and repo.detalhe_lead(lead.id)["status"] == "bot"


def test_pediu_humano_quando_o_cliente_pede_passa(t, lead, resumo_fixo):
    repo.salvar_mensagem(lead.id, "cliente", "Quero falar com um atendente de verdade")
    r = json.loads(t["transferir_humano"].invoke({"motivo": "pediu_humano"}))
    assert r["ok"] is True and repo.detalhe_lead(lead.id)["status"] == "humano"


def test_transferir_humano_motivo_invalido_e_rejeitado(t, resumo_fixo):
    with pytest.raises(Exception):
        t["transferir_humano"].invoke({"motivo": "outro"})


def test_lead_transferido_aparece_primeiro_no_painel(t, lead, resumo_fixo):
    t["transferir_humano"].invoke({"motivo": "urgencia", "urgencia": "alta"})
    primeiro = repo.listar_leads()[0]
    assert primeiro["status"] == "humano" and primeiro["urgencia"] == "alta"


# ---------- definição das tools (o que o modelo enxerga) ----------

def test_tools_expostas_ao_modelo():
    nomes = {t.name for t in tools_mod.criar_tools(1)}
    assert nomes == {"buscar_estoque", "detalhes_carro", "transferir_humano"}
    for t in tools_mod.criar_tools(1):
        assert t.description, f"{t.name} sem descrição"
