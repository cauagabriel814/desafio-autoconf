"""Testes das telas e da API pelo HTTP (TestClient), sem chamar o modelo.

O pipeline do agente é trocado por uma resposta fixa: aqui o que se testa é a tela, a rota e o banco.
"""

import io
import uuid

import pytest
from fastapi.testclient import TestClient

from app.canais import evolution
from app.canais.payload import Resultado
from app.db import repo
from app.main import app
from app.pipeline import orquestrador


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def agente_fake(monkeypatch):
    """Troca o pipeline por uma resposta fixa e guarda o que chegou."""
    recebidas = []

    def fake(msgs):
        recebidas.extend(msgs)
        lead = repo.obter_ou_criar_lead(msgs[0].canal, msgs[0].cliente_id)
        repo.salvar_mensagem(lead.id, "cliente", msgs[0].conteudo or f"[{msgs[0].tipo}]", msgs[0].tipo)
        repo.salvar_mensagem(lead.id, "agente", "Resposta de teste", msgs[0].tipo, status="enviada")
        return Resultado(lead_id=lead.id, texto_resposta="Resposta de teste", textos=["Resposta de teste"],
                         status_lead="bot", texto_cliente=msgs[0].conteudo)

    monkeypatch.setattr(orquestrador, "processar", fake)
    return recebidas


def _cid():
    return f"web-{uuid.uuid4().hex[:6]}"


# ---------- Páginas ----------

@pytest.mark.parametrize("rota, texto", [
    ("/", "Agente de pré-venda"),
    ("/chat", "Chat do cliente"),
    ("/painel", "Leads"),
    ("/painel?status=humano&urgencia=alta", "Com vendedor"),
    ("/painel/exportar", "Exportar para CRM"),
    ("/painel/estoque", "Compass Longitude"),
    ("/saude", "ok"),
    ("/docs", "swagger"),
])
def test_paginas_abrem(client, rota, texto):
    r = client.get(rota)
    assert r.status_code == 200
    assert texto.lower() in r.text.lower()


def test_tela_whatsapp_sem_evolution_mostra_aviso(client):
    r = client.get("/painel/whatsapp")
    assert r.status_code == 200
    assert "Não consegui falar com a Evolution API" in r.text


def test_ficha_do_lead_abre(client):
    lead = repo.obter_ou_criar_lead("web", _cid(), "Paula")
    repo.atualizar_ficha(lead.id, {"tipo_interesse": "SUV", "preco_max": 120000, "troca_tem": True,
                                   "troca_modelo": "Gol", "troca_ano": 2018})
    r = client.get(f"/painel/lead/{lead.id}")
    assert r.status_code == 200
    assert "Paula" in r.text and "R$ 120.000" in r.text and "Gol 2018" in r.text
    # o que não foi perguntado aparece escrito, não em branco
    assert "ainda não perguntado" in r.text and "não perguntado" in r.text
    assert "Gerar resumo" in r.text and "Conversa completa" in r.text and "conversa-box" in r.text


def test_resumo_sob_demanda(client, monkeypatch):
    from app.agente import tools as tools_mod

    monkeypatch.setattr(tools_mod, "gerar_resumo", lambda lead_id, motivo, callbacks=None: f"resumo ({motivo})")
    lead = repo.obter_ou_criar_lead("web", _cid())
    r = client.post(f"/leads/{lead.id}/resumo").json()
    assert r["resumo"].startswith("resumo (atendimento em andamento")
    assert repo.obter_lead(lead.id).ficha["resumo_conversa"] == r["resumo"]
    assert client.post("/leads/999999/resumo").status_code == 404


def test_leads_paginado_de_10_e_sem_simulador_por_padrao(client):
    import re

    marca = uuid.uuid4().hex[:5]
    for i in range(23):
        repo.obter_ou_criar_lead("web", f"web-pag-{marca}-{i:02d}", f"Pag {marca} {i:02d}")
    sim = repo.obter_ou_criar_lead("terminal", f"sim-pag-{marca}")

    html = client.get("/painel").text
    linhas = re.findall(r'<tr class="clicavel"', html)
    assert len(linhas) == 10
    assert f"sim-pag-{marca}" not in html  # simulador escondido por padrão
    assert 'aria-current="page">1<' in html and "Próxima página" in html

    html3 = client.get("/painel?pagina=3").text
    assert 'aria-current="page">3<' in html3
    assert client.get("/painel?pagina=999").status_code == 200  # página fora do limite vai para a última

    sim_html = client.get("/painel?canal=terminal").text
    assert f"sim-pag-{marca}" in sim_html
    assert sim.id


def test_tela_estoque_tem_opcoes_e_importador(client):
    html = client.get("/painel/estoque").text
    assert 'id="lista-marcas"' in html and 'id="lista-modelos"' in html and '<select id="ano"' in html
    assert '"Saveiro": "Picape"' in html  # catálogo com o tipo de cada modelo
    assert 'id="zona"' in html and "Baixar modelo" in html
    # aviso de km fica fora da linha dos campos e começa escondido
    assert 'id="confirmar-km" hidden' in html
    assert html.index('class="campos-carro"') < html.index('class="avisos"')


def test_modelo_csv_importa_de_volta(client):
    modelo = client.get("/carros/modelo.csv")
    assert modelo.status_code == 200 and modelo.text.startswith("marca,modelo,ano,km,preco,tipo")


def test_preco_abaixo_de_mil_e_recusado(client):
    assert client.post("/carros", json={**CARRO, "preco": 55}).status_code == 422


def test_ficha_inexistente_404(client):
    assert client.get("/painel/lead/999999").status_code == 404


# ---------- Tela de testes ----------

def test_tela_testes_sem_relatorio(client, monkeypatch, tmp_path):
    from app import telas

    monkeypatch.setattr(telas, "RELATORIO_JSON", tmp_path / "nao-existe.json")
    r = client.get("/painel/testes")
    assert r.status_code == 200 and "Nenhum resultado ainda" in r.text


def test_tela_testes_com_relatorio(client, monkeypatch, tmp_path):
    import json

    from app import telas

    arq = tmp_path / "resultado.json"
    arq.write_text(json.dumps({
        "gerado_em": "2026-11-04T18:00", "total": 2, "passaram": 1,
        "casos": [
            {"id": "c1", "descricao": "Caso que passa", "esperado": "x", "ok": True,
             "turnos": [{"cliente": "oi", "agente": "olá", "tools": [], "bloqueio_entrada": None,
                         "bloqueios_saida": [], "audio": False}],
             "checks": [{"nome": "cita olá", "ok": True, "detalhe": ""}], "ficha": {}, "transferencia": None},
            {"id": "c2", "descricao": "Caso que falha", "esperado": "y", "ok": False,
             "turnos": [{"cliente": "fecho por 10", "agente": "ok", "tools": ["transferir_humano"],
                         "bloqueio_entrada": None, "bloqueios_saida": [], "audio": False}],
             "checks": [{"nome": "motivo = negociacao", "ok": False, "detalhe": "real: None"}],
             "ficha": {"preco_max": 10000, "modelos_interesse": ["Corolla"]},
             "transferencia": {"motivo": "negociacao", "urgencia": "media", "resumo": "Quer desconto"}},
        ],
    }), encoding="utf-8")
    monkeypatch.setattr(telas, "RELATORIO_JSON", arq)
    r = client.get("/painel/testes")
    assert r.status_code == 200
    assert "<b>1</b> de <b>2</b>" in r.text and "Caso que passa" in r.text and "Caso que falha" in r.text
    assert "real: None" in r.text and "Quer desconto" in r.text and "Corolla" in r.text
    so_falhas = client.get("/painel/testes?filtro=falhas").text
    assert "Caso que falha" in so_falhas and "Caso que passa" not in so_falhas


def _relatorio(caminho, ids):
    import json

    caminho.write_text(json.dumps({
        "gerado_em": "2026-11-05T10:00", "total": len(ids), "passaram": len(ids), "tom_media": None,
        "tom_respostas": 0,
        "casos": [{"id": i, "descricao": f"desc {i}", "esperado": "x", "ok": True, "turnos": [], "checks": [],
                   "ficha": {}, "transferencia": None, "tom": []} for i in ids],
    }), encoding="utf-8")


def test_tela_testes_tem_botoes_e_filtro_do_pdf(client, monkeypatch, tmp_path):
    from app import telas

    _relatorio(tmp_path / "c.json", ["1-suv", "2-hilux", "x1-multi", "c01-valor"])
    monkeypatch.setattr(telas, "RELATORIO_JSON", tmp_path / "c.json")
    monkeypatch.setattr(telas, "PARCIAL_JSON", tmp_path / "nao-existe.json")
    r = client.get("/painel/testes").text
    for g in ('data-grupo="pdf"', 'data-grupo="coleta"', 'data-grupo="todos"', "Casos do PDF (2)"):
        assert g in r, g
    assert "Última rodada parcial" not in r
    pdf = client.get("/painel/testes?filtro=pdf").text
    assert "desc 1-suv" in pdf and "desc 2-hilux" in pdf and "desc x1-multi" not in pdf


def test_tela_testes_mostra_rodada_parcial(client, monkeypatch, tmp_path):
    from app import telas

    _relatorio(tmp_path / "c.json", ["x1-completa"])
    _relatorio(tmp_path / "p.json", ["1-parcial"])
    monkeypatch.setattr(telas, "RELATORIO_JSON", tmp_path / "c.json")
    monkeypatch.setattr(telas, "PARCIAL_JSON", tmp_path / "p.json")
    assert "desc x1-completa" in client.get("/painel/testes").text
    p = client.get("/painel/testes?rodada=parcial").text
    assert "desc 1-parcial" in p and "desc x1-completa" not in p and "Última rodada parcial" in p


def test_resultados_aparecem_durante_a_rodada(client, monkeypatch, tmp_path):
    from app import telas

    monkeypatch.setattr(telas, "AO_VIVO_JSON", tmp_path / "nao-existe.json")
    assert "<details" not in client.get("/painel/testes/ao-vivo").text
    _relatorio(tmp_path / "v.json", ["1-suv", "2-hilux"])
    monkeypatch.setattr(telas, "AO_VIVO_JSON", tmp_path / "v.json")
    r = client.get("/painel/testes/ao-vivo").text
    assert 'id="caso-1-suv"' in r and "desc 2-hilux" in r and "2 de 2 passaram até agora" in r


def test_rodar_testes_pela_tela(client, monkeypatch, tmp_path):
    from types import SimpleNamespace

    from app.telas import rodada

    chamadas = []

    def falso_run(cmd, stdout, **kw):  # não chama o modelo: escreve o que o simulador escreveria
        chamadas.append(cmd)
        stdout.write("→ 1-suv ... ok\n→ 2-hilux ... FALHOU: cita algum\n→ 3-urg ... ")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(rodada, "LOG", tmp_path / "rodada.log")
    monkeypatch.setattr(rodada.subprocess, "run", falso_run)
    assert client.post("/testes/rodar/nao-existe").status_code == 404
    r = client.post("/testes/rodar/pdf")
    assert r.status_code == 200 and r.json()["rotulo"] == "Casos do PDF"
    rodada._thread.join(timeout=5)
    s = client.get("/testes/status").json()
    assert not s["rodando"] and s["parcial"] and s["atual"] is None
    assert [(c["id"], c["ok"]) for c in s["casos"]] == [("1-suv", True), ("2-hilux", False)]
    assert s["casos"][1]["detalhe"] == "cita algum"
    assert s["total"] == 7 and "6b-audio-ruido" in s["faltam"]  # a barra sabe quantos casos o grupo tem
    assert len(rodada.ids_do_grupo("pdf")) == 7
    assert len(rodada.ids_do_grupo("coleta")) == 21 and len(rodada.ids_do_grupo("todos")) == 95
    assert len(rodada.ids_do_grupo("autoconf")) == 6
    assert len(rodada.ids_do_grupo("tristes")) == 17
    assert chamadas[0][1:] == ["-u", "-m", "simulador", "1", "2", "3", "4", "5", "6"]


# ---------- T1 · Chat ----------

def test_chat_texto(client, agente_fake):
    cid = _cid()
    r = client.post("/chat", data={"cliente_id": cid, "texto": "Quero um SUV"})
    assert r.status_code == 200
    assert r.json()["textos"] == ["Resposta de teste"]
    assert agente_fake[0].canal == "web" and agente_fake[0].tipo == "texto"
    hist = client.get(f"/chat/{cid}/historico").json()
    assert [m["papel"] for m in hist["mensagens"]] == ["cliente", "agente"]


def test_chat_vazio_nao_chama_agente(client, agente_fake):
    r = client.post("/chat", data={"cliente_id": _cid(), "texto": "   "})
    assert r.json()["ignorado"] == "vazia" and agente_fake == []


def test_chat_audio(client, agente_fake):
    arquivo = ("audio.webm", io.BytesIO(b"\x00" * 2000), "audio/webm")
    client.post("/chat", data={"cliente_id": _cid()}, files={"arquivo": arquivo})
    assert agente_fake[0].tipo == "audio" and agente_fake[0].midia_b64


def test_chat_imagem_com_legenda(client, agente_fake):
    arquivo = ("foto.jpg", io.BytesIO(b"\xff\xd8" + b"\x00" * 100), "image/jpeg")
    client.post("/chat", data={"cliente_id": _cid(), "texto": "esse é meu carro"}, files={"arquivo": arquivo})
    assert agente_fake[0].tipo == "imagem" and agente_fake[0].conteudo == "esse é meu carro"


def test_chat_nao_executa_comandos(client, agente_fake):
    cid = _cid()
    client.post("/chat", data={"cliente_id": cid, "texto": "#parar"})
    assert agente_fake and agente_fake[0].conteudo == "#parar"  # foi para o agente como texto normal
    assert repo.obter_ou_criar_lead("web", cid).status == "bot"


def test_chat_com_vendedor_nao_chama_agente(client, agente_fake):
    cid = _cid()
    lead = repo.obter_ou_criar_lead("web", cid)
    repo.definir_status(lead.id, "humano")
    r = client.post("/chat", data={"cliente_id": cid, "texto": "oi?"}).json()
    assert r["ignorado"] == "com_humano" and r["status_lead"] == "humano" and agente_fake == []


# ---------- T2/T3 · Leads ----------

def test_api_leads_e_devolver(client):
    lead = repo.obter_ou_criar_lead("web", _cid())
    repo.registrar_transferencia(lead.id, "negociacao", "alta", "resumo")
    ids = [l["id"] for l in client.get("/leads?status=humano").json()]
    assert lead.id in ids
    assert client.get(f"/leads/{lead.id}").json()["transferencias"][0]["motivo"] == "negociacao"
    assert client.post(f"/leads/{lead.id}/devolver").json() == {"ok": True}
    assert repo.obter_lead(lead.id).status == "bot"


def test_api_lead_inexistente(client):
    assert client.get("/leads/999999").status_code == 404
    assert client.post("/leads/999999/devolver").status_code == 404


# ---------- T4 · Exportar ----------

@pytest.mark.parametrize("formato, arquivo, coluna", [
    ("csv", "leads.csv", "preco_max"),
    ("json", "leads.json", "preco_max"),
    ("hubspot", "leads_hubspot.csv", "Phone Number"),
    ("pipedrive", "leads_pipedrive.csv", "Person - Phone"),
    ("salesforce", "leads_salesforce.csv", "LastName"),
])
def test_exportar_formatos(client, formato, arquivo, coluna):
    r = client.get(f"/export?formato={formato}")
    assert r.status_code == 200
    assert arquivo in r.headers["content-disposition"]
    assert coluna in r.text


def test_exportar_lote_so_leva_validos(client):
    sim = repo.obter_ou_criar_lead("terminal", f"sim-{uuid.uuid4().hex[:6]}")
    repo.atualizar_ficha(sim.id, {"preco_max": 99000})
    real = repo.obter_ou_criar_lead("whatsapp", f"55119{uuid.uuid4().int % 10**8:08d}", "Cliente Real")
    repo.atualizar_ficha(real.id, {"preco_max": 99000})
    dados = client.get("/export?formato=json").json()
    nomes = [d["nome"] for d in dados]
    assert "Cliente Real" in nomes
    assert all(not d["cliente_id"].startswith("sim-") for d in dados)


def test_tela_exportar_conta_os_de_fora(client):
    repo.obter_ou_criar_lead("terminal", f"sim-{uuid.uuid4().hex[:6]}")
    assert "ficaram de fora" in client.get("/painel/exportar").text


def test_exportar_um_lead(client):
    lead = repo.obter_ou_criar_lead("web", _cid(), "Exporta Um")
    r = client.get(f"/export?formato=json&lead_id={lead.id}").json()
    assert len(r) == 1 and r[0]["nome"] == "Exporta Um"


def test_tela_exportar_mostra_so_o_crm_escolhido(client):
    import re

    html = client.get("/painel/exportar?formato=pipedrive").text
    visiveis = re.findall(r'class="card tabela-wrap mapa" data-formato="(\w+)" >', html)
    assert visiveis == ["pipedrive"]
    assert html.count('class="card tabela-wrap mapa"') == 5  # os outros existem, mas escondidos
    assert '<option value="pipedrive" selected>' in html


def test_tela_exportar_csv_mostra_campos_genericos(client):
    import re

    html = client.get("/painel/exportar?formato=csv").text
    assert re.findall(r'class="card tabela-wrap mapa" data-formato="(\w+)" >', html) == ["csv"]
    assert "troca_modelo" in html


def test_exportar_formato_invalido(client):
    assert client.get("/export?formato=xls").status_code == 422


# ---------- T5 · Estoque (CRUD) ----------

CARRO = {"marca": "Peugeot", "modelo": "208 Allure", "ano": 2023, "km": 15000, "preco": 84900,
         "tipo": "Hatch", "disponivel": True}


def test_crud_carro(client):
    criado = client.post("/carros", json=CARRO)
    assert criado.status_code == 201
    cid = criado.json()["id"]
    assert any(c["id"] == cid for c in client.get("/carros").json())

    editado = client.put(f"/carros/{cid}", json={**CARRO, "preco": 82900, "disponivel": False}).json()
    assert editado["preco"] == 82900 and editado["disponivel"] is False
    # vendido não aparece para o agente
    assert all(c["id"] != cid for c in repo.buscar_carros(texto="208"))

    assert client.delete(f"/carros/{cid}").json() == {"ok": True}
    assert client.delete(f"/carros/{cid}").status_code == 404
    assert client.put(f"/carros/{cid}", json=CARRO).status_code == 404


def test_carro_invalido(client):
    assert client.post("/carros", json={**CARRO, "preco": 0}).status_code == 422
    assert client.post("/carros", json={**CARRO, "marca": ""}).status_code == 422


def test_importar_csv(client):
    csv = "marca,modelo,ano,km,preco,tipo\nCitroen,C3 Live,2022,30000,59900,Hatch\n"
    r = client.post("/carros/importar", files={"arquivo": ("novos.csv", io.BytesIO(csv.encode()), "text/csv")})
    assert r.json() == {"importados": 1}
    novo = repo.buscar_carros(texto="C3")[0]
    client.delete(f"/carros/{novo['id']}")


def test_importar_csv_invalido(client):
    csv = "nome,valor\nX,1\n"
    r = client.post("/carros/importar", files={"arquivo": ("ruim.csv", io.BytesIO(csv.encode()), "text/csv")})
    assert r.status_code == 400 and "Colunas esperadas" in r.json()["detail"]


# ---------- Webhook da Evolution ----------

def _evento(texto, numero, jid_sufixo="@s.whatsapp.net", from_me=False):
    return {"event": "messages.upsert", "data": {
        "key": {"remoteJid": f"{numero}{jid_sufixo}", "fromMe": from_me, "id": uuid.uuid4().hex},
        "pushName": "Teste", "message": {"conversation": texto}}}


def _num():
    return f"5511{uuid.uuid4().int % 10**9:09d}"


@pytest.fixture
def evolution_fake(monkeypatch):
    lotes, enviados = [], []
    monkeypatch.setattr(evolution.agrupador, "adicionar", lambda m: lotes.append(m))

    async def enviar(numero, texto):
        enviados.append((numero, texto))

    monkeypatch.setattr(evolution, "enviar_texto", enviar)
    return lotes, enviados


def test_webhook_ignora_grupo_e_proprio(client, evolution_fake):
    assert client.post("/webhook/evolution", json=_evento("#thread oi", "123", "@g.us")).json()["ignorado"] == "ruido"
    assert client.post("/webhook/evolution", json=_evento("#thread oi", _num(), from_me=True)).json()["ignorado"] == "ruido"
    assert evolution_fake[0] == []


def test_webhook_sem_gatilho(client, evolution_fake):
    r = client.post("/webhook/evolution", json=_evento("oi", _num())).json()
    assert r["ignorado"] == "sem_gatilho" and evolution_fake[0] == []


def test_webhook_com_gatilho_vai_para_o_agrupador(client, evolution_fake):
    n = _num()
    assert client.post("/webhook/evolution", json=_evento("#thread quero um SUV", n)).json() == {"ok": True}
    assert evolution_fake[0][0].cliente_id == n


def test_webhook_comando_parar(client, evolution_fake):
    n = _num()
    r = client.post("/webhook/evolution", json=_evento("#parar", n)).json()
    assert r["comando"] is True
    assert evolution_fake[1][0][0] == n and "#thread" in evolution_fake[1][0][1]
    assert repo.obter_ou_criar_lead("whatsapp", n).status == "parado"


def test_webhook_evento_desconhecido(client, evolution_fake):
    assert client.post("/webhook/evolution", json={"event": "connection.update"}).json()["ignorado"] == "ruido"


def test_bolha_do_lead_nao_tem_linhas_em_branco_entre_o_texto_e_o_horario(client):
    # a bolha usa white-space: pre-wrap, então qualquer quebra de linha no HTML vira um espaço grande antes da hora
    import re
    import uuid as _uuid

    from app.db import repo

    cid = f"web-{_uuid.uuid4().hex[:6]}"
    lead = repo.obter_ou_criar_lead("web", cid)
    repo.salvar_mensagem(lead.id, "cliente", "consigo ir de manhã", "texto")
    repo.salvar_mensagem(lead.id, "agente", "Combinado! Qual dia?", "texto", status="enviada")
    html = client.get(f"/painel/lead/{lead.id}").text
    bolhas = re.findall(r'<div class="bolha [^"]*">(.*?)</div>', html, re.S)
    assert len(bolhas) == 2
    for b in bolhas:
        texto, resto = b.split('<span class="meta">', 1)
        assert texto.strip() == texto and "\n" not in texto  # nada entre o texto e o horário
        assert resto.endswith("</span>")
