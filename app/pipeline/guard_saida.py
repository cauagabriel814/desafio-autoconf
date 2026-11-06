"""Etapa 06: guardrail de saída. Checagem em código contra o banco + LLM juiz."""

import logging
import re
from typing import Literal

from pydantic import BaseModel

from app import llm
from app.agente import prompts
from app.db import repo

log = logging.getLogger(__name__)

_RE_REAIS = re.compile(r"R\$\s*(\d{1,3}(?:\.\d{3})+|\d+)(?:,\d{2})?")
_RE_MIL = re.compile(r"(\d{1,3}(?:[.,]\d{1,3})?)\s*mil\b", re.IGNORECASE)


# Assunto fora do tema é barrado na entrada; o juiz de saída fica só com o que tem evidência concreta.
Regra = Literal["promessa", "contraproposta", "vazamento"]


class Veredito(BaseModel):
    aprovado: bool
    motivo: str
    regra: Regra | None = None
    trecho: str = ""


# Para aceitar uma reprovação do juiz, o trecho citado precisa ter evidência da regra
_EVIDENCIA = {
    "promessa": r"desconto|aprovad|aprova[cç]|garant|parcela|juros|taxa|reserv|segur(o|ar|ei)|prazo|entreg",
    # o preço em si já é conferido no código; aqui só vale linguagem de ACEITAR/propor outro valor
    "contraproposta": r"fechad|fecho|fa[cç]o (por|o|a|em)|aceit|consigo (fazer|fechar|deixar)|deixo (por|em|a)"
                      r"|pode ser (por|em)|sai por .* (pra|para) voc|abaixo (pra|para)|chego (a|em|no)",
    "vazamento": r"prompt|instru[cç]|ferrament|\btools?\b|buscar_estoque|detalhes_carro|transferir_humano|"
                 r"sistema|regras internas|fui programad|meu c[oó]digo",
}

# Modelos conhecidos no Brasil. Se a resposta oferecer um que não está no estoque, o código barra.
MODELOS_CONHECIDOS = [
    "hilux", "sw4", "amarok", "frontier", "l200", "triton", "saveiro", "strada", "montana", "maverick", "oroch",
    "gol", "voyage", "fox", "up", "virtus", "jetta", "nivus", "taos", "tiguan", "polo", "t-cross",
    "onix", "prisma", "cruze", "tracker", "equinox", "spin", "s10", "trailblazer",
    "uno", "mobi", "argo", "cronos", "pulse", "fastback", "toro", "palio", "siena",
    "hb20", "hb20s", "creta", "tucson", "santa fe", "ix35",
    "ka", "fiesta", "focus", "ecosport", "territory", "ranger", "bronco",
    "corolla", "corolla cross", "yaris", "etios", "rav4",
    "civic", "city", "fit", "hr-v", "wr-v", "cr-v", "zr-v",
    "kicks", "versa", "sentra", "march",
    "kwid", "sandero", "logan", "duster", "captur", "stepway",
    "compass", "renegade", "commander",
    "208", "2008", "3008", "c3", "c4 cactus", "aircross",
    "golf", "fusca", "dolphin", "song", "seal", "tiggo", "haval", "outlander", "lancer", "asx",
]
_NEGACAO = re.compile(r"\bn[aã]o\b|\bnenhum|infelizmente|\bsem\b|n[aã]o temos|em falta|esgotad", re.IGNORECASE)


def valores_citados(texto: str) -> set[int]:
    """Valores em reais citados: "R$ 112.900", "112900", "120 mil", "112,9 mil"."""
    valores: set[int] = set()
    for m in _RE_REAIS.finditer(texto):
        valores.add(int(m.group(1).replace(".", "")))
    for m in _RE_MIL.finditer(texto):
        bruto = m.group(1)
        if "." in bruto and len(bruto.split(".")[1]) == 3:  # "120.000 mil" não acontece; trata como milhar
            valores.add(int(bruto.replace(".", "")))
        else:
            valores.add(int(round(float(bruto.replace(",", ".")) * 1000)))
    return valores


def checar_valores(resposta: str, permitidos: set[int]) -> str | None:
    """Devolve o motivo se a resposta citar um valor que não existe no estoque nem na conversa."""
    for v in valores_citados(resposta):
        if v < 1000:
            continue
        if v not in permitidos:
            return f"valor R$ {v:,} não existe no estoque nem foi citado pelo cliente".replace(",", ".")
    return None


def checar_preco_do_carro(resposta: str, carros: list[dict], do_cliente: set[int]) -> str | None:
    """Frase que cita UM carro do estoque e um valor em reais: o valor tem que ser o preço desse carro.

    Pega o caso "Compass por R$ 129.900", em que o valor existe no estoque (é o Sentra) mas não é o do Compass.
    """
    for frase in re.split(r"(?<=[.!?\n])\s+|\n", resposta):
        f = _sem_acento(frase)
        citados = []
        for c in carros:
            chave = _sem_acento(c["modelo"].split()[0])  # "Compass", "T-Cross", "Corolla"
            if re.search(rf"(?<![\w-]){re.escape(chave)}(?![\w-])", f):
                citados.append(c)
        # "Corolla" e "Corolla Cross": fica com o nome mais específico que aparece na frase
        especificos = [c for c in citados if _sem_acento(c["modelo"]) in f] or citados
        if len({c["id"] for c in especificos}) != 1:
            continue  # nenhum ou vários carros na frase: não dá para saber de quem é o valor
        carro = especificos[0]
        for v in valores_citados(frase):
            if v >= 1000 and v not in (carro["preco"], carro["km"]) and v not in do_cliente:
                return f"preço do {carro['nome']} é R$ {carro['preco']:,}, não R$ {v:,}".replace(",", ".")
    return None


def _valores_do_cliente(lead_id: int) -> set[int]:
    vals: set[int] = set()
    for m in repo.historico(lead_id, 50):
        if m.papel == "cliente":
            vals |= valores_citados(m.texto)
            vals |= {int(n) for n in re.findall(r"\b\d{4,6}\b", m.texto)}
    lead = repo.obter_lead(lead_id)
    for k in ("preco_min", "preco_max", "entrada"):
        if isinstance((lead.ficha or {}).get(k), int):
            vals.add(lead.ficha[k])
    return vals


def _permitidos(lead_id: int) -> set[int]:
    vals: set[int] = set()
    for c in repo.buscar_carros(apenas_disponiveis=False):
        vals.update({c["preco"], c["km"]})
    for m in repo.historico(lead_id, 50):
        if m.papel == "cliente":
            vals |= valores_citados(m.texto)
            vals |= {int(n) for n in re.findall(r"\b\d{4,6}\b", m.texto)}
    lead = repo.obter_lead(lead_id)
    for k in ("preco_min", "preco_max", "entrada"):
        if isinstance((lead.ficha or {}).get(k), int):
            vals.add(lead.ficha[k])
    return vals


def _estoque_texto() -> str:
    linhas = []
    for c in repo.buscar_carros():
        preco = f"{c['preco']:,}".replace(",", ".")
        km = f"{c['km']:,}".replace(",", ".")
        linhas.append(f"- {c['nome']} {c['ano']} · {c['tipo']} · {km} km · R$ {preco}")
    return "\n".join(linhas)


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip().lower()


def _reprovacao_fundamentada(v: Veredito, resposta: str) -> bool:
    """O juiz precisa citar um trecho que existe na resposta E que tenha evidência da regra violada."""
    trecho = _norm(v.trecho.strip(" \"'“”"))
    if len(trecho) < 4 or trecho not in _norm(resposta):
        return False
    if v.regra not in _EVIDENCIA:
        return False
    return bool(re.search(_EVIDENCIA[v.regra], trecho, re.IGNORECASE))


def _sem_acento(t: str) -> str:
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn").lower()


def checar_modelos(resposta: str, nomes_estoque: list[str], permitidos: list[str] | None = None) -> str | None:
    """Barra a resposta que oferece um modelo conhecido que não está no estoque.

    Permitido: dizer que NÃO tem ("não temos Hilux") e citar o carro de troca do cliente (`permitidos`).
    """
    estoque = _sem_acento(" | ".join(nomes_estoque + (permitidos or [])))
    for frase in re.split(r"(?<=[.!?\n])\s+", resposta):
        f = _sem_acento(frase)
        for modelo in MODELOS_CONHECIDOS:
            if not re.search(rf"(?<![\w-]){re.escape(modelo)}(?![\w-])", f):
                continue
            if re.search(rf"(?<![\w-]){re.escape(modelo)}(?![\w-])", estoque):
                continue  # existe no estoque
            if _NEGACAO.search(frase):
                continue  # está dizendo que não tem
            return f"oferece {modelo} que não está no estoque"
    return None


def verificar(resposta: str, lead_id: int, mensagem_cliente: str, callbacks: list | None = None) -> Veredito:
    if not resposta.strip():
        return Veredito(aprovado=False, motivo="resposta vazia")
    motivo = checar_valores(resposta, _permitidos(lead_id))
    if motivo:
        return Veredito(aprovado=False, motivo=f"código: {motivo}")
    motivo = checar_preco_do_carro(resposta, repo.buscar_carros(apenas_disponiveis=False), _valores_do_cliente(lead_id))
    if motivo:
        return Veredito(aprovado=False, motivo=f"código: {motivo}")
    troca = (repo.obter_lead(lead_id).ficha or {}).get("troca_modelo")
    motivo = checar_modelos(resposta, [c["nome"] for c in repo.buscar_carros()], [troca] if troca else [])
    if motivo:
        return Veredito(aprovado=False, motivo=f"código: {motivo}")
    try:
        juiz = llm.chat(temperature=0).with_structured_output(Veredito)
        v = juiz.invoke(
            prompts.JUIZ_SAIDA.format(estoque=_estoque_texto(), mensagem=mensagem_cliente, resposta=resposta),
            config={"callbacks": callbacks or [], "run_name": "guardrail_saida"},
        )
        if not v.aprovado and not _reprovacao_fundamentada(v, resposta):
            log.info("Reprovação do juiz descartada (trecho não encontrado na resposta): %s", v.motivo)
            return Veredito(aprovado=True, motivo=f"reprovação sem trecho válido descartada: {v.motivo}")
        return v
    except llm.SemChaveOpenAI:
        raise
    except Exception:
        log.exception("Juiz de saída falhou, aprovando pela checagem de código")
        return Veredito(aprovado=True, motivo="juiz indisponível")


def reescrever(resposta: str, motivo: str, mensagem_cliente: str, callbacks: list | None = None) -> str:
    r = llm.chat(temperature=0.3).invoke(
        prompts.REESCRITA.format(motivo=motivo, estoque=_estoque_texto(), mensagem=mensagem_cliente, resposta=resposta),
        config={"callbacks": callbacks or [], "run_name": "reescrita"},
    )
    return r.content.strip()
