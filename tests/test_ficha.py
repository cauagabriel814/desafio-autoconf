"""Regra de qualificação: extração aplicada à ficha e checklist (sem chamar o modelo)."""

import uuid

from app.agente.ficha import Extracao, aplicar, checklist, pendentes
from app.db import repo


def _lead():
    return repo.obter_ou_criar_lead("terminal", f"ficha-{uuid.uuid4().hex[:6]}")


# ---------- aplicar extração ----------

def test_aplicar_grava_e_mescla():
    l = _lead()
    aplicar(l.id, Extracao(tipo_interesse="SUV", preco_max=120000))
    aplicar(l.id, Extracao(urgencia="alta"))
    f = repo.obter_lead(l.id).ficha
    assert f["tipo_interesse"] == "SUV" and f["preco_max"] == 120000 and f["urgencia"] == "alta"


def test_aplicar_nao_apaga_com_campo_nulo():
    l = _lead()
    aplicar(l.id, Extracao(preco_max=120000))
    aplicar(l.id, Extracao())  # mensagem sem dado novo ("ok", "beleza")
    assert repo.obter_lead(l.id).ficha["preco_max"] == 120000


def test_aplicar_troca_marca_troca_tem():
    l = _lead()
    aplicar(l.id, Extracao(troca_modelo="VW Gol", troca_ano=2018))
    f = repo.obter_lead(l.id).ficha
    assert f["troca_tem"] is True and f["troca_ano"] == 2018


def test_aplicar_entrada_implica_financiar():
    l = _lead()
    aplicar(l.id, Extracao(entrada=20000))
    assert repo.obter_lead(l.id).ficha["quer_financiar"] is True


def test_aplicar_nome_vai_para_o_lead():
    l = _lead()
    aplicar(l.id, Extracao(nome="Carla"))
    assert repo.obter_lead(l.id).nome == "Carla"


def test_aplicar_acumula_modelos():
    l = _lead()
    aplicar(l.id, Extracao(modelos_interesse=["Creta Limited"]))
    aplicar(l.id, Extracao(modelos_interesse=["T-Cross Comfortline", "Creta Limited"]))
    assert repo.obter_lead(l.id).ficha["modelos_interesse"] == ["Creta Limited", "T-Cross Comfortline"]


def test_nao_sabe_so_vale_para_o_item_perguntado():
    l = _lead()
    aplicar(l.id, Extracao(tipo_interesse="SUV"))  # próximo item: faixa_preco
    aplicar(l.id, Extracao(nao_sabe=["faixa_preco", "troca", "urgencia", "pagamento"]))
    assert repo.obter_lead(l.id).ficha["nao_informado"] == ["faixa_preco"]


def test_nao_sabe_de_item_nao_perguntado_e_ignorado():
    l = _lead()
    aplicar(l.id, Extracao(nao_sabe=["urgencia"]))  # o perguntado era o interesse
    assert "nao_informado" not in repo.obter_lead(l.id).ficha


def test_troca_com_modelo_forca_troca_tem_e_nao_vira_interesse():
    l = _lead()
    aplicar(l.id, Extracao(troca_tem=False, troca_modelo="HB20", troca_ano=2019, modelos_interesse=["HB20"]))
    f = repo.obter_lead(l.id).ficha
    assert f["troca_tem"] is True and f["troca_modelo"] == "HB20"
    assert "modelos_interesse" not in f


def test_troca_mais_interesse_mantem_o_interesse():
    l = _lead()
    aplicar(l.id, Extracao(troca_modelo="Onix", troca_ano=2019, tipo_interesse="SUV", modelos_interesse=["Onix"]))
    f = repo.obter_lead(l.id).ficha
    assert f["tipo_interesse"] == "SUV" and "modelos_interesse" not in f


def test_mudou_interesse():
    from app.agente.ficha import mudou_interesse

    antes = {"tipo_interesse": "SUV", "preco_max": 110000, "modelos_interesse": ["Creta Limited"]}
    assert not mudou_interesse(antes, Extracao(preco_max=110000))  # só repetiu
    assert not mudou_interesse(antes, Extracao(quer_financiar=True))
    assert mudou_interesse(antes, Extracao(preco_max=130000))
    assert mudou_interesse(antes, Extracao(tipo_interesse="Sedã"))
    assert mudou_interesse(antes, Extracao(modelos_interesse=["Kicks"]))
    # o mesmo carro escrito de outro jeito não é interesse novo (a lista voltava depois do "à vista")
    escolheu = {"tipo_interesse": "SUV", "preco_max": 120000, "modelos_interesse": ["Kicks"]}
    assert not mudou_interesse(escolheu, Extracao(modelos_interesse=["Nissan Kicks"]))
    assert mudou_interesse(escolheu, Extracao(modelos_interesse=["Creta"]))


# ---------- checklist ----------

def test_compra_na_ordem_valor_modelo_pagamento_visita():
    # o valor vem primeiro: com ele dá para listar tudo o que cabe e o cliente escolhe vendo a lista
    c = checklist({})
    assert "⬜ Valor  ← PRÓXIMA PERGUNTA" in c
    assert pendentes({}) == ["faixa_preco", "interesse", "pagamento", "visita"]
    assert pendentes({"preco_max": 100000}) == ["interesse", "pagamento", "visita"]


def test_caso_1_do_pdf_falta_so_a_visita():
    # "Quero um SUV até 120 mil, tenho um Gol 2018 pra troca e quero financiar." responde as 3 de uma vez;
    # a qualificação só fecha com a visita marcada (resposta da Autoconf)
    from app.agente.ficha import qualificado

    f = {"tipo_interesse": "SUV", "preco_max": 120000, "quer_financiar": True,
         "troca_tem": True, "troca_modelo": "Gol", "troca_ano": 2018}
    c = checklist(f)
    assert "✅ Modelo / tipo de carro: SUV" in c
    assert "✅ Valor: até R$ 120.000" in c
    assert "✅ Como quer comprar: financiar" in c
    assert "Carro na troca: Gol 2018" in c  # aparece como informação extra, não como pergunta
    assert pendentes(f) == ["visita"] and not qualificado(f)
    assert "⬜ Visita na loja  ← PRÓXIMA PERGUNTA" in c and "hora de convidar" in c
    f["visita"] = "sábado de manhã"
    assert pendentes(f) == [] and qualificado(f)


def test_venda_pergunta_o_carro_e_a_visita_para_avaliar():
    from app.agente.ficha import qualificado, situacao_campos

    f = {"intencao": "vender"}
    assert pendentes(f) == ["carro_venda", "visita"]
    assert "qual é o carro que ele quer vender" in checklist(f)
    f.update(troca_tem=True, troca_modelo="Gol")
    assert "de que ano é o Gol" in checklist(f) and not qualificado(f)
    f.update(troca_ano=2018, visita="amanhã à tarde")
    assert pendentes(f) == [] and qualificado(f)
    s = {c["rotulo"]: c["valor"] for c in situacao_campos(f)}
    assert s["Carro que quer vender"] == "Gol 2018" and s["Visita para avaliação"] == "amanhã à tarde"
    assert "Carro na troca: Gol" not in checklist(f)  # o carro dele não é troca


def test_troca_anotada_nao_some_sem_o_cliente_dizer():
    # caso x35: a extração marcou troca_tem=false com a Saveiro já anotada
    l = _lead()
    aplicar(l.id, Extracao(troca_tem=True, troca_modelo="Saveiro"), "tenho uma Saveiro pra troca")
    aplicar(l.id, Extracao(troca_tem=False, troca_ano=2017), "É 2017")
    f = repo.obter_lead(l.id).ficha
    assert f["troca_tem"] is True and f["troca_ano"] == 2017
    aplicar(l.id, Extracao(troca_tem=False), "não vou dar carro na troca, desisti")
    assert repo.obter_lead(l.id).ficha["troca_tem"] is False


def test_vendendo_o_carro_dito_e_o_dele():
    # caso a1: "É um Gol 2018" depois de "quero vender o meu" virou interesse de compra
    l = _lead()
    aplicar(l.id, Extracao(intencao="vender"), "Vocês compram carro? Quero vender o meu")
    aplicar(l.id, Extracao(modelos_interesse=["Gol"]), "É um Gol 2018")
    f = repo.obter_lead(l.id).ficha
    assert f["intencao"] == "vender" and f["troca_modelo"] == "Gol" and f["troca_ano"] == 2018
    assert not f.get("modelos_interesse")


def test_valor_so_com_valor_na_mensagem():
    # caso c21: "pagar a vista" virou preco_max=76000 (preço do Cronos, copiado da conversa)
    l = _lead()
    aplicar(l.id, Extracao(preco_max=70000), "70 mil")
    ext = Extracao(preco_max=76000, quer_financiar=False)
    aplicar(l.id, ext, "pagar a vista")
    assert ext.preco_max is None  # não dispara lista nova
    f = repo.obter_lead(l.id).ficha
    assert f["preco_max"] == 70000 and f["quer_financiar"] is False


def test_falar_do_carro_que_quer_comprar_vira_compra():
    l = _lead()
    aplicar(l.id, Extracao(intencao="vender", troca_modelo="Gol", troca_ano=2018))
    aplicar(l.id, Extracao(tipo_interesse="SUV"))
    assert repo.obter_lead(l.id).ficha["intencao"] == "comprar"


def test_nao_pergunta_entrada_troca_prazo_idade_renda():
    f = {"tipo_interesse": "Hatch", "preco_max": 70000, "quer_financiar": True}
    assert pendentes(f) == ["visita"]
    c = checklist(f)
    for item in ("Entrada  ←", "Urgência  ←", "Idade  ←", "Faixa de renda  ←", "Carro na troca  ←"):
        assert item not in c


def test_extras_ditos_pelo_cliente_aparecem_sem_pergunta():
    f = {"tipo_interesse": "SUV", "troca_tem": False, "entrada": 20000, "urgencia": "alta"}
    c = checklist(f)
    assert "Também já sabemos" in c and "Carro na troca: não tem" in c and "Entrada: R$ 20.000" in c
    assert pendentes(f) == ["faixa_preco", "pagamento", "visita"]


def test_nao_sabe_nao_volta_a_perguntar():
    f = {"tipo_interesse": "SUV", "nao_informado": ["faixa_preco"]}
    c = checklist(f)
    assert "✅ Valor: cliente não soube" in c
    assert pendentes(f)[0] == "pagamento"


def test_completo_e_qualificado():
    from app.agente.ficha import qualificado

    f = {"tipo_interesse": "SUV", "preco_max": 120000, "quer_financiar": False,
         "troca_tem": False, "urgencia": "media", "visita": "sábado"}
    assert pendentes(f) == [] and qualificado(f)
    c = checklist(f)
    assert "lead QUALIFICADO" in c and "PRÓXIMA PERGUNTA" not in c


def test_nao_sabe_nao_qualifica():
    from app.agente.ficha import qualificado

    # sem a forma de pagamento (cliente "não soube"): segue em frente, mas não é lead qualificado
    f = {"tipo_interesse": "Sedã", "preco_max": 100000, "nao_informado": ["pagamento"], "visita": "sábado"}
    assert pendentes(f) == [] and not qualificado(f)
    f2 = {"tipo_interesse": "Sedã", "preco_max": 100000, "perguntas_feitas": {"pagamento": 2}, "visita": "sábado"}
    assert pendentes(f2) == [] and not qualificado(f2)
    # não quis marcar a visita: segue ajudando, mas não qualifica
    f3 = {"tipo_interesse": "Sedã", "preco_max": 100000, "quer_financiar": False, "nao_informado": ["visita"]}
    assert pendentes(f3) == [] and not qualificado(f3)


def test_nao_sabe_so_vale_com_duvida_na_mensagem():
    l = _lead()
    aplicar(l.id, Extracao(tipo_interesse="Sedã", preco_max=100000))  # próxima: pagamento
    aplicar(l.id, Extracao(nao_sabe=["pagamento"]), "#thread voltei, quais vocês têm?")
    assert "nao_informado" not in repo.obter_lead(l.id).ficha
    aplicar(l.id, Extracao(nao_sabe=["pagamento"]), "ainda não decidi se financio")
    assert repo.obter_lead(l.id).ficha["nao_informado"] == ["pagamento"]


def test_incompleto_nao_e_qualificado():
    from app.agente.ficha import qualificado

    assert not qualificado({"tipo_interesse": "SUV", "preco_max": 120000})
    assert not qualificado({})  # ficha vazia não conta, mesmo com tudo "não informado"
    assert not qualificado({"nao_informado": ["interesse", "faixa_preco", "pagamento", "troca", "urgencia"]})


def test_acao_obrigatoria_qualificado():
    from app.agente.ficha import acao_obrigatoria

    a = acao_obrigatoria("qualificado", {"visita": "sábado de manhã"})
    assert 'motivo "qualificado"' in a and "anotou sábado de manhã" in a and "confirmar o horário" in a
    assert "avaliação" in acao_obrigatoria("qualificado", {"intencao": "vender", "visita": "segunda"})


def test_acao_obrigatoria_sem_resposta():
    from app.agente.ficha import acao_obrigatoria

    a = acao_obrigatoria("sem_resposta")
    assert 'motivo "sem_resposta"' in a and "Não invente" in a


def test_incompleto_proibe_oferecer_vendedor():
    assert "NÃO ofereça vendedor" in checklist({"tipo_interesse": "SUV"})


def test_acao_obrigatoria_reclamacao():
    a = __import__("app.agente.ficha", fromlist=["acao_obrigatoria"]).acao_obrigatoria("reclamacao")
    assert "transferir_humano" in a and "reclamacao" in a and "sem oferecer carro" in a


def test_transferir_nao_vai_para_a_ficha():
    l = _lead()
    aplicar(l.id, Extracao(preco_max=90000, transferir="negociacao"))
    assert "transferir" not in repo.obter_lead(l.id).ficha


def test_so_o_uso_e_parcial_e_pergunta_o_tipo():
    f = {"uso": "família", "preco_max": 100000}
    assert pendentes(f)[0] == "interesse"
    c = checklist(f)
    assert "🟡 Modelo / tipo de carro: uso: família (falta o tipo de carro)  ← PRÓXIMA PERGUNTA" in c
    assert "SUV, sedã, hatch ou picape" in c


# ---------- itens parciais (respondidos só em parte) ----------

BASE = {"tipo_interesse": "SUV", "preco_max": 100000, "quer_financiar": False, "visita": "sábado"}


def test_troca_sem_ano_pergunta_o_ano():
    f = {**BASE, "troca_tem": True, "troca_modelo": "Saveiro"}
    assert pendentes(f)[0] == "troca"
    c = checklist(f)
    assert "🟡 Carro na troca: Saveiro (falta o ano)  ← PRÓXIMA PERGUNTA: pergunte de que ano é o Saveiro" in c
    assert "não repita a parte que ele já disse" in c


def test_troca_sem_modelo_pergunta_o_modelo():
    f = {**BASE, "troca_tem": True, "troca_ano": 2017}
    assert "🟡 Carro na troca: ano 2017 (falta o modelo)" in checklist(f)


def test_tem_troca_vago_nao_segura_a_qualificacao():
    from app.agente.ficha import qualificado

    f = {**BASE, "troca_tem": True}  # "tenho troca", sem dizer o carro
    assert pendentes(f) == [] and qualificado(f)


def test_situacao_de_cada_campo():
    from app.agente.ficha import ficha_para_resumo, situacao_campos

    f = {"tipo_interesse": "SUV", "preco_max": 100000, "perguntas_feitas": {"pagamento": 1},
         "troca_tem": True, "troca_modelo": "Saveiro"}
    s = {c["rotulo"]: (c["valor"], c["situacao"]) for c in situacao_campos(f)}
    assert s["Modelo / tipo de carro"] == ("SUV", "respondido")
    assert s["Valor"] == ("até R$ 100.000", "respondido")
    assert s["Como quer comprar"] == ("perguntado, sem resposta", "sem_resposta")
    assert s["Carro na troca"] == ("Saveiro (ano não informado)", "respondido")
    assert s["Entrada"] == ("não perguntado", "nao_perguntado")
    assert s["Idade"] == ("não perguntado", "nao_perguntado")
    vazia = {c["rotulo"]: c["valor"] for c in situacao_campos({})}
    assert vazia["Modelo / tipo de carro"] == "ainda não perguntado"
    assert situacao_campos({"nao_informado": ["faixa_preco"]})[1]["valor"] == "cliente não soube"
    texto = ficha_para_resumo(f)
    assert "- Entrada: não perguntado" in texto and "- Como quer comprar: perguntado, sem resposta" in texto


def test_resposta_em_baloes():
    from app.pipeline.resposta import em_baloes, tempo_digitando

    t = ("Opa, SUV é ótima pedida!\n\n- Creta 2022 · R$ 112.900\n- T-Cross 2023 · R$ 118.500\n\n"
         "O T-Cross é o mais novinho.\n\nE você pensa em pagar à vista ou financiar?")
    b = em_baloes(t)
    assert len(b) == 3 and b[1].startswith("- Creta") and "T-Cross 2023" in b[1]
    assert b[2].endswith("financiar?")
    assert em_baloes("Oi! Tudo bem?") == ["Oi! Tudo bem?"]
    assert 800 <= tempo_digitando("oi") <= tempo_digitando("x" * 200) <= 4000


def test_troca_completa_nao_pergunta_nada():
    f = {**BASE, "troca_tem": True, "troca_modelo": "Saveiro", "troca_ano": 2017}
    assert "Carro na troca: Saveiro 2017" in checklist(f) and pendentes(f) == []


def test_qualificado_nao_passa_com_troca_parcial():
    f = {**BASE, "troca_tem": True, "troca_modelo": "Saveiro", "urgencia": "baixa"}
    assert pendentes(f) == ["troca"]


# ---------- trava anti-laço ----------

def test_item_perguntado_duas_vezes_e_deixado_de_lado():
    from app.agente.ficha import registrar_pergunta

    l = _lead()
    aplicar(l.id, Extracao(tipo_interesse="SUV"))  # próxima: faixa_preco
    registrar_pergunta(l.id, repo.obter_lead(l.id).ficha)
    assert pendentes(repo.obter_lead(l.id).ficha)[0] == "faixa_preco"  # 1 vez: ainda pergunta
    registrar_pergunta(l.id, repo.obter_lead(l.id).ficha)
    f = repo.obter_lead(l.id).ficha
    assert "faixa_preco" not in pendentes(f)
    assert "já perguntado 2 vezes sem resposta" in checklist(f)


def test_parcial_tambem_tem_trava():
    f = {**BASE, "troca_tem": True, "troca_modelo": "Saveiro", "perguntas_feitas": {"troca": 2}}
    assert "troca" not in pendentes(f)
    assert "✅ Carro na troca (Saveiro (falta o ano))" in checklist(f)


def test_motivo_da_transferencia_sempre_legivel_no_resumo(monkeypatch):
    from types import SimpleNamespace

    from app import llm
    from app.agente import tools
    from app.agente.ficha import MOTIVO_PARA_VENDEDOR, motivo_legivel

    assert set(MOTIVO_PARA_VENDEDOR) >= {"reclamacao", "negociacao", "pedido_credito", "urgencia", "pediu_humano",
                                         "sem_resposta", "qualificado"}
    assert "reclamou" in motivo_legivel("reclamacao") and "visita" in motivo_legivel("qualificado")
    assert motivo_legivel("outro_motivo") == "outro motivo"  # desconhecido: não quebra

    class _Chat:
        def invoke(self, *_a, **_k):
            return SimpleNamespace(content="Quer um SUV até 120 mil.")

    monkeypatch.setattr(llm, "chat", lambda *a, **k: _Chat())
    l = repo.obter_ou_criar_lead("web", "resumo-motivo")
    r = tools.gerar_resumo(l.id, "negociacao")
    assert r.startswith("Motivo da transferência: ") and "negociar" in r and r.endswith("Quer um SUV até 120 mil.")
    # sem motivo conhecido (resumo avulso): só o texto do modelo
    assert tools.gerar_resumo(l.id, "atendimento em andamento") == "Quer um SUV até 120 mil."


def test_sem_preferencia_conta_como_resposta_do_interesse():
    f = {"preco_max": 100000, "sem_preferencia": True}
    assert pendentes(f) == ["pagamento", "visita"]
    assert "sem preferência" in checklist(f)


def test_sem_dados_quando_nao_da_para_buscar_no_estoque():
    from app.agente.ficha import sem_dados

    assert sem_dados({"nao_informado": ["faixa_preco", "interesse"]})
    assert sem_dados({"nao_informado": ["faixa_preco"], "sem_preferencia": True})  # "tanto faz" e sem valor
    assert sem_dados({"perguntas_feitas": {"faixa_preco": 2, "interesse": 2}})      # perguntou 2x, nada
    assert not sem_dados({"nao_informado": ["faixa_preco"], "tipo_interesse": "SUV"})  # disse o tipo: dá para listar
    assert not sem_dados({"preco_max": 80000, "nao_informado": ["interesse"]})  # tem valor: lista o que cabe
    assert not sem_dados({"intencao": "vender", "nao_informado": ["faixa_preco", "interesse"]})
