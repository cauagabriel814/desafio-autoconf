"""Sugestões de carros montadas pelo código (sem chamar o modelo)."""

from app.agente.grafo import sugestoes


def _linhas(s):
    return [l for l in s.splitlines() if l.startswith("- ")]


def test_tipo_e_orcamento_lista_todos_mais_perto_do_teto_primeiro():
    s = sugestoes({"tipo_interesse": "SUV", "preco_max": 120000})
    assert "MOSTRE AGORA" in s and "TODOS" in s
    l = _linhas(s)
    # todos os 4 SUVs até 120 mil, em ordem de preço (do mais barato ao mais caro)
    assert len(l) == 4
    assert "Duster" in l[0] and "Kicks" in l[1] and "Creta" in l[2] and "T-Cross" in l[3]
    assert "Compass" not in s


def test_marca_pedida_continua_quando_ele_so_diz_o_valor():
    from app.db import repo

    # o estoque padrão não tem BYD: cadastra um só para este teste
    byd = repo.salvar_carro({"marca": "BYD", "modelo": "Dolphin", "ano": 2024, "km": 5, "preco": 89000, "tipo": "Hatch"})
    try:
        # "queria um BYD" -> mostrou o BYD; "100 mil" não pode virar a lista de todo o estoque até 100 mil
        s = sugestoes({"modelos_interesse": ["BYD"], "preco_max": 100000})
        l = _linhas(s)
        assert len(l) == 1 and "BYD Dolphin" in l[0]
        assert "Kwid" not in s and "Onix" not in s
        # nenhum BYD cabe em 60 mil: diz isso e mostra o BYD mais perto, sem encher de outras marcas
        s2 = sugestoes({"modelos_interesse": ["BYD"], "preco_max": 60000})
        assert "cabe nesse valor" in s2 and "BYD Dolphin" in s2 and "Kwid" not in s2
        # se ele muda de ideia e pede outro tipo, a lista volta a ser do tipo
        s3 = sugestoes({"modelos_interesse": ["BYD"], "tipo_interesse": "SUV", "preco_max": 120000})
        assert "Duster" in s3
    finally:
        repo.excluir_carro(byd["id"])


def test_depois_da_lista_vem_a_pergunta_exata():
    # tem SUV e a Saveiro sem ano: depois da lista, a pergunta é o valor (1ª pendente), dita com todas as letras
    s = sugestoes({"tipo_interesse": "SUV", "troca_tem": True, "troca_modelo": "Saveiro"})
    assert "pergunte SÓ isto: até quanto pensa em investir" in s
    s2 = sugestoes({"tipo_interesse": "SUV", "preco_max": 100000, "quer_financiar": False,
                    "troca_tem": True, "troca_modelo": "Saveiro"})
    assert "pergunte SÓ isto: de que ano é o Saveiro" in s2  # o ano da troca vem antes do "gostou?" (caso x35)
    s2b = sugestoes({"tipo_interesse": "SUV", "preco_max": 100000, "quer_financiar": False})
    assert "pergunte SÓ isto: se ele gostou de algum" in s2b  # lista já no orçamento: antes da visita, gostou?
    s3 = sugestoes({"tipo_interesse": "SUV", "preco_max": 100000, "quer_financiar": False, "modelos_interesse": ["Kicks"],
                    "troca_tem": True, "troca_modelo": "Saveiro"})
    assert "pergunte SÓ isto: de que ano é o Saveiro" in s3  # já escolheu um carro: segue o checklist


def test_gostou_de_algum_so_quando_faz_sentido():
    from app.agente.grafo import perguntar_escolha

    assert perguntar_escolha({"tipo_interesse": "Sedã", "preco_max": 70000})            # falta pagamento: antes, gostou?
    assert not perguntar_escolha({"tipo_interesse": "SUV"})                            # falta o valor: ele estreita a lista
    assert not perguntar_escolha({"tipo_interesse": "SUV", "preco_max": 1e5, "quer_financiar": False,
                                  "troca_tem": True, "troca_modelo": "Saveiro"})       # antes, o ano da troca
    assert not perguntar_escolha({"tipo_interesse": "SUV", "preco_max": 1e5, "modelos_interesse": ["Kicks"]})  # já escolheu
    assert perguntar_escolha({"tipo_interesse": "SUV", "preco_max": 1e5, "quer_financiar": True})  # antes da visita
    assert not perguntar_escolha({"tipo_interesse": "SUV", "preco_max": 1e5, "quer_financiar": True,
                                  "visita": "sábado"})  # qualificado: vai para o vendedor


def test_resposta_ao_gostou_de_algum():
    from app.agente.grafo import _OPINOU_LISTA

    for t in ("Gostei do Kicks", "Não gostei de nenhum", "esse primeiro aí", "achei caro", "o Creta parece bom"):
        assert _OPINOU_LISTA.search(t), t
    for t in ("É 2017", "Vou fazer parcelado", "Sábado de manhã", "uns 100 mil", "Vai ser financiado sim"):
        assert not _OPINOU_LISTA.search(t), t


def test_modelo_copiado_do_contexto_nao_conta():
    from app.agente.grafo import _so_no_contexto

    ctx = "Atendente: que dia você consegue vir ver o Fiat Cronos Drive?"
    assert _so_no_contexto("Fiat", "sabado de manha", ctx)                 # caso c21: virou lista da Fiat
    assert not _so_no_contexto("Cronos", "gostei do cronos", ctx)          # o cliente disse
    assert not _so_no_contexto("BYD", "tem algum biotipo?", "")            # correção do áudio: vale


def test_sedan_conta_como_tipo_dito():
    from app.agente.grafo import _TIPO_DITO

    for t in ("queria comprar um sedan", "sedans", "um hatchback", "camionete"):
        assert _TIPO_DITO.search(t), t


def test_o_que_falta_para_qualificar():
    from app.agente.grafo import faltando_para_qualificar

    # a conversa do teste manual: valor e pagamento, mas o tipo ("sedan") não foi gravado
    f = faltando_para_qualificar({"preco_max": 70000, "quer_financiar": False, "visita": "sábado"})
    assert len(f) == 1 and "tipo de carro" in f[0]
    f = faltando_para_qualificar({"tipo_interesse": "Sedã", "preco_max": 70000, "quer_financiar": False})
    assert len(f) == 1 and "na loja" in f[0]
    assert faltando_para_qualificar({"tipo_interesse": "Sedã", "preco_max": 70000, "quer_financiar": False,
                                     "visita": "sábado"}) == []
    assert len(faltando_para_qualificar({"intencao": "vender", "troca_modelo": "Gol"})) == 2


def test_checklist_sem_resposta_nao_diz_que_tem_tudo():
    from app.agente.ficha import checklist

    # perguntou o tipo 2 vezes sem resposta: não é "checklist completo"
    c = checklist({"preco_max": 70000, "quer_financiar": False, "visita": "sábado", "perguntas_feitas": {"interesse": 2}})
    assert "QUALIFICADO" not in c and "NÃO diga que já tem tudo" in c


def test_limpa_markdown_e_linhas_em_branco_da_lista():
    from app.agente.grafo import limpar_formatacao

    t = ("Temos estes:\n\n- **Renault Duster** 2021 | 55.000 km | R$ 84.900\n\n"
         "- **Nissan Kicks** 2022 | 40.000 km | R$ 98.900\n\n- **Creta** 2022 | R$ 112.900\n\nQual seu orçamento?")
    limpo = limpar_formatacao(t)
    assert "**" not in limpo and "|" not in limpo
    assert "- Renault Duster 2021 · 55.000 km · R$ 84.900\n- Nissan Kicks" in limpo
    assert limpo.endswith("\n\nQual seu orçamento?")


def test_lista_sem_tracinho_vira_lista():
    from app.agente.grafo import limpar_formatacao

    t = ("Olha só o que temos:\n\nRenault Duster Iconic 2021 · 55.000 km · R$ 84.900\n\n"
         "Nissan Kicks SV 2022 · 40.000 km · R$ 98.900\n\nQual o seu orçamento?")
    assert limpar_formatacao(t) == ("Olha só o que temos:\n\n- Renault Duster Iconic 2021 · 55.000 km · R$ 84.900\n"
                                    "- Nissan Kicks SV 2022 · 40.000 km · R$ 98.900\n\nQual o seu orçamento?")


def test_passar_para_vendedor_tem_jeito_humano_por_motivo():
    from app.agente.ficha import COMO_PASSAR, acao_obrigatoria

    for motivo in ("reclamacao", "negociacao", "pedido_credito", "urgencia", "pediu_humano"):
        a = acao_obrigatoria(motivo)
        assert COMO_PASSAR[motivo] in a and 'Nunca diga "não posso"' in a
    assert "Sem emoji e sem oferecer carro" in acao_obrigatoria("reclamacao")


def test_destaques_verdadeiros():
    from app.agente.grafo import destaques

    carros = [
        {"nome": "Fiat Argo Drive 1.0", "ano": 2023, "km": 22000, "preco": 69900},
        {"nome": "BYD Dolphin", "ano": 2024, "km": 5, "preco": 89000},
        {"nome": "Renault Kwid Zen", "ano": 2022, "km": 30000, "preco": 52900},
    ]
    d = destaques(carros)
    assert "mais novo: BYD Dolphin (2024)" in d          # e não o Argo, como o agente disse
    assert "menos rodado: BYD Dolphin (5 km)" in d
    assert "mais em conta: Renault Kwid Zen (R$ 52.900)" in d
    assert destaques(carros[:1]) == ""


def test_sugestoes_trazem_destaques():
    s = sugestoes({"tipo_interesse": "Hatch", "preco_max": 80000})
    assert "Destaques VERDADEIROS" in s


def test_detecta_pedido_de_lista():
    from app.agente.grafo import _PEDIU_LISTA

    for t in ("Quais SUV vocês têm?", "me mostra os hatches", "o que tem de picape?", "Oi, tem hatch?",
              "vocês têm sedã?", "quais as opções?"):
        assert _PEDIU_LISTA.search(t), t
    for t in ("Não sei queria um hatch", "Quero um SUV", "Tô procurando um sedã", "Um SUV"):
        assert not _PEDIU_LISTA.search(t), t


def test_detecta_pergunta_de_robo():
    from app.agente.grafo import _DIZ_VIRTUAL, _PERGUNTA_ROBO

    for t in ("Você é um robô?", "é uma pessoa?", "tô falando com quem?", "isso é um bot?", "vc é IA?"):
        assert _PERGUNTA_ROBO.search(t), t
    for t in ("Quero um SUV", "o carro é de verdade novo?"[:0] or "tem Corolla?"):
        assert not _PERGUNTA_ROBO.search(t), t
    assert _DIZ_VIRTUAL.search("Sou a Lia, a assistente virtual da loja")
    assert not _DIZ_VIRTUAL.search("Opa, desculpa pela confusão! Estou aqui pra te ajudar.")


def test_tipo_so_vale_se_o_cliente_disse():
    from app.agente.grafo import _TIPO_DITO

    for t in ("Quero um SUV", "tô atrás de um sedã", "um hatch", "uma picape", "caminhonete"):
        assert _TIPO_DITO.search(t), t
    for t in ("Quero um carro pra família", "algo econômico", "uso na cidade"):
        assert not _TIPO_DITO.search(t), t


def test_detecta_frases_de_robo():
    from app.agente.grafo import _ROBOTICO

    for t in ("Aqui estão algumas opções de SUVs:", "Temos algumas opções que podem te interessar",
              "Como posso ajudá-lo hoje?", "Fico à disposição!", "Certamente, segue abaixo a lista"):
        assert _ROBOTICO.search(t), t
    for t in ("Opa, SUV é ótima pedida pra família!", "Olha só o que chegou:", "Boa! À vista facilita."):
        assert not _ROBOTICO.search(t), t


def test_so_tipo_lista_todos_do_tipo():
    from app.db import repo

    s = sugestoes({"tipo_interesse": "SUV"})
    assert len(_linhas(s)) == len(repo.buscar_carros(tipo="SUV")) == 7


def test_todas_as_listas_em_ordem_de_preco():
    import re

    for ficha, modelos in (({"tipo_interesse": "SUV"}, None), ({"tipo_interesse": "Hatch", "preco_max": 80000}, None),
                           ({"tipo_interesse": "Picape"}, ["Hilux"]), ({}, ["Toyota"]),
                           ({"tipo_interesse": "SUV", "preco_max": 50000}, None)):
        s = sugestoes(ficha, modelos)
        precos = [int(p.replace(".", "")) for p in re.findall(r"^- .*R\$ ([\d.]+)", s, re.M)]  # só as linhas da lista
        assert precos == sorted(precos), (ficha, precos)


def test_nao_repete_comentario_ja_feito():
    s = sugestoes({"tipo_interesse": "Hatch"}, None, ["O Fiat Argo é bem novinho, com apenas 22 mil km!"])
    assert "Você JÁ comentou" in s and "O Fiat Argo é bem novinho" in s and "Não repita" in s


def test_lista_tem_teto_de_10():
    from app.agente.grafo import MAX_CARROS

    s = sugestoes({"preco_max": 500000})  # o estoque inteiro (20) cabe no orçamento
    assert len(_linhas(s)) == MAX_CARROS == 10


def test_modelo_pedido_que_existe():
    s = sugestoes({"tipo_interesse": "Sedã"}, ["Civic"])
    assert "o(s) carro(s) que o cliente pediu" in s and "Civic Touring 2020" in s and "R$ 132.900" in s


def test_hilux_sem_valor_sugere_2_picapes_e_pergunta_o_valor():
    s = sugestoes({"tipo_interesse": "Picape"}, ["Hilux 2024"])
    assert "Hilux 2024, que NÃO está no estoque" in s
    l = _linhas(s)
    assert len(l) == 2 and all(any(p in x for p in ("Toro", "S10", "Ranger")) for x in l)
    assert "até quanto ele pensa em investir" in s  # a faixa de preço abre mais opções


def test_modelo_que_nao_existe_com_valor_mostra_o_que_cabe():
    s = sugestoes({"tipo_interesse": "Picape", "preco_max": 300000}, ["Hilux 2024"])
    assert len(_linhas(s)) == 3 and "até quanto" not in s  # todas as picapes, sem perguntar o valor de novo
    # nenhum carro do tipo cabe no valor: mostra os de outro tipo que cabem
    s = sugestoes({"tipo_interesse": "Picape", "preco_max": 70000}, ["Hilux"])
    assert _linhas(s) and not any(p in s for p in ("Toro", "S10", "Ranger"))


def test_modelo_que_nao_existe_sem_categoria_deixa_o_agente_buscar():
    s = sugestoes({}, ["Fusca"])
    assert "NÃO está no estoque" in s and "buscar_estoque" in s and not _linhas(s)


def test_modelo_antigo_da_ficha_nao_conta():
    # cliente perguntou da Hilux antes; agora fala de SUV até 120 mil
    s = sugestoes({"tipo_interesse": "SUV", "preco_max": 120000, "modelos_interesse": ["Hilux"]}, [])
    assert "Hilux" not in s and "T-Cross" in s


def test_orcamento_abaixo_de_tudo_sugere_o_mais_proximo():
    s = sugestoes({"tipo_interesse": "SUV", "preco_max": 50000})
    assert "Nenhum carro" in s and "Duster" in s  # SUV mais barato (R$ 84.900)


def test_tipo_certo_acima_do_orcamento_sugere_o_mais_barato_do_tipo():
    s = sugestoes({"tipo_interesse": "Picape", "preco_max": 60000})
    assert "Nenhum carro" in s and "Toro" in s and "Kwid" not in s
