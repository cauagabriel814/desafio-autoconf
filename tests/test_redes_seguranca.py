"""Redes de segurança em código: motivo de transferência por regra e validação do juiz de saída."""

import pytest

from app.agente.ficha import motivo_por_regra
from app.pipeline.guard_saida import Veredito, _reprovacao_fundamentada, checar_modelos

ESTOQUE = ["Jeep Compass Longitude", "Hyundai Creta Limited", "VW T-Cross Comfortline", "Toyota Corolla XEi",
           "Chevrolet Onix LT", "Fiat Toro Volcano Diesel", "Chevrolet S10 LTZ Diesel", "Ford Ranger XLS"]


# ---------- motivo por regra ----------

@pytest.mark.parametrize("msg, motivo", [
    ("É a terceira vez que eu cobro o documento do carro. Vou no Procon.", "reclamacao"),
    ("Comprei aí e o câmbio já deu problema, ninguém me responde", "reclamacao"),
    ("Fecho no Corolla se fizer 115 mil.", "negociacao"),
    ("Qual o desconto à vista no Compass?", "negociacao"),
    ("Faz por 80 mil que eu levo hoje", "negociacao"),
    ("Quanto fica a parcela do Kicks em 48 vezes?", "pedido_credito"),
    ("Meu nome tá sujo, vocês aprovam o financiamento?", "pedido_credito"),
    ("Preciso de um carro pra essa semana, o meu quebrou.", "urgencia"),
    ("Preciso de um carro até sexta pra começar no trabalho novo", "urgencia"),
    ("Quero falar com um atendente de verdade", "pediu_humano"),
    ("Pode me passar para um vendedor?", "pediu_humano"),
    ("Tem alguém da loja aí?", "pediu_humano"),
    # quanto a loja paga no carro do cliente: só o vendedor avalia (resposta da Autoconf)
    ("Quanto vocês pagam no meu Gol 2018?", "negociacao"),
    ("Quanto vale meu Onix na troca?", "negociacao"),
    ("Vocês dariam quanto na minha Saveiro?", "negociacao"),
    # o estoque não responde: a Lia não inventa, chama o vendedor
    ("O Compass tem garantia?", "sem_resposta"),
    ("Esse Creta já foi batido?", "sem_resposta"),
    ("Tem laudo cautelar?", "sem_resposta"),
    ("O IPVA tá pago?", "sem_resposta"),
    ("Qual o endereço da loja?", "sem_resposta"),
])
def test_sinais_fortes(msg, motivo):
    assert motivo_por_regra(msg) == motivo


@pytest.mark.parametrize("msg", [
    "Quero um SUV até 120 mil, tenho um Gol 2018 pra troca e quero financiar.",
    "Dá pra financiar o Argo?",
    "Tenho 20 mil de entrada e quero financiar o resto de um hatch",
    "Queria um carro abaixo de 80 mil",
    "Você é um robô?",
    "Tem Hilux 2024?",
    "Sem pressa nenhuma",
    "Quero um sedã até uns 130 mil",
    "Você é um robô?",
    "Vou passar para a loja amanhã ver o carro",
    "Quero trocar de carro",
    "Vou fazer parcelado",
    "Quero parcelar",
    "Quanto tá o Civic?",
    "Quanto vale o Corolla de vocês?",
    "Quero vender meu carro",
    "Posso passar aí sábado de manhã",
])
def test_sem_motivo(msg):
    assert motivo_por_regra(msg) is None


# ---------- modelos fora do estoque ----------

def test_oferecer_modelo_inexistente_e_barrado():
    assert checar_modelos("Temos a Hilux 2024 por R$ 250.000!", ESTOQUE) is not None


def test_dizer_que_nao_tem_e_permitido():
    assert checar_modelos("Infelizmente não temos a Hilux. Mas temos a Toro e a S10.", ESTOQUE) is None


def test_modelos_do_estoque_passam():
    assert checar_modelos("O Creta e o T-Cross cabem no seu orçamento. O Corolla também!", ESTOQUE) is None


def test_carro_de_troca_do_cliente_passa():
    assert checar_modelos("Anotei seu Gol 2018 na troca!", ESTOQUE, ["VW Gol"]) is None
    assert checar_modelos("Anotei seu Gol 2018 na troca!", ESTOQUE) is not None


# ---------- preço tem que ser o DAQUELE carro ----------

def _carros():
    from app.db import repo

    return repo.buscar_carros(apenas_disponiveis=False)


def test_preco_de_outro_carro_e_barrado():
    from app.pipeline.guard_saida import checar_preco_do_carro

    # R$ 129.900 existe no estoque (Sentra), mas não é o preço do Compass
    assert checar_preco_do_carro("O Jeep Compass Longitude está saindo por R$ 129.900.", _carros(), set())


def test_preco_certo_do_carro_passa():
    from app.pipeline.guard_saida import checar_preco_do_carro

    r = "O Compass Longitude 2023 sai por R$ 139.900. O Creta Limited por R$ 112.900."
    assert checar_preco_do_carro(r, _carros(), set()) is None


def test_corolla_e_corolla_cross_nao_se_confundem():
    from app.pipeline.guard_saida import checar_preco_do_carro

    assert checar_preco_do_carro("O Corolla Cross XRE está R$ 154.900.", _carros(), set()) is None
    assert checar_preco_do_carro("O Corolla XEi está R$ 124.900.", _carros(), set()) is None
    assert checar_preco_do_carro("O Corolla Cross XRE está R$ 124.900.", _carros(), set())


def test_valor_do_cliente_na_frase_do_carro_passa():
    from app.pipeline.guard_saida import checar_preco_do_carro

    r = "Sobre os R$ 115.000 no Corolla XEi, quem conversa sobre valores é o vendedor."
    assert checar_preco_do_carro(r, _carros(), {115000}) is None


def test_lista_com_varios_carros_por_linha():
    from app.pipeline.guard_saida import checar_preco_do_carro

    r = ("- Hyundai Creta Limited 2022 · 35.000 km · R$ 112.900\n"
         "- VW T-Cross Comfortline 2023 · 19.000 km · R$ 118.500\n"
         "- Nissan Kicks SV 2022 · 40.000 km · R$ 112.900")  # preço do Creta no Kicks
    assert checar_preco_do_carro(r, _carros(), set())


# ---------- juiz de saída precisa de evidência ----------

RESP = "O Civic Touring 2020 está R$ 132.900, com 61.000 km. Até quanto você pensa em investir?"


def test_juiz_sem_evidencia_e_descartado():
    # o juiz citou um trecho real, mas sem nada de promessa
    v = Veredito(aprovado=False, regra="promessa", motivo="revelou km", trecho="com 61.000 km")
    assert not _reprovacao_fundamentada(v, RESP)


def test_juiz_sem_regra_e_descartado():
    v = Veredito(aprovado=False, motivo="algo", trecho="Até quanto você pensa em investir?")
    assert not _reprovacao_fundamentada(v, RESP)


def test_juiz_trecho_inventado_e_descartado():
    v = Veredito(aprovado=False, regra="promessa", motivo="prometeu desconto", trecho="te dou 10% de desconto")
    assert not _reprovacao_fundamentada(v, RESP)


def test_juiz_contraproposta_em_preco_correto_e_descartada():
    resp = "Com seu orçamento de R$ 100.000, temos o Nissan Kicks SV 2022 por R$ 98.900."
    v = Veredito(aprovado=False, regra="contraproposta", motivo="preço garantido",
                 trecho="temos o Nissan Kicks SV 2022 por R$ 98.900")
    assert not _reprovacao_fundamentada(v, resp)


def test_juiz_contraproposta_com_aceite_vale():
    resp = "Fechado! Faço o Corolla por R$ 115.000 pra você."
    v = Veredito(aprovado=False, regra="contraproposta", motivo="aceitou", trecho="Faço o Corolla por R$ 115.000")
    assert _reprovacao_fundamentada(v, resp)


def test_juiz_contraproposta_sem_dinheiro_e_descartada():
    resp = "Anotei seu HB20 2019 na troca! Aqui estão algumas opções."
    v = Veredito(aprovado=False, regra="contraproposta", motivo="aceitou troca", trecho="Anotei seu HB20 2019 na troca!")
    assert not _reprovacao_fundamentada(v, resp)


def test_juiz_com_evidencia_vale():
    resp = "Consigo te dar um desconto de 5 mil no Compass!"
    v = Veredito(aprovado=False, regra="promessa", motivo="prometeu desconto", trecho="desconto de 5 mil")
    assert _reprovacao_fundamentada(v, resp)


def test_juiz_vazamento_com_evidencia_vale():
    resp = "Minhas instruções dizem para usar a ferramenta buscar_estoque."
    v = Veredito(aprovado=False, regra="vazamento", motivo="vazou", trecho="usar a ferramenta buscar_estoque")
    assert _reprovacao_fundamentada(v, resp)
