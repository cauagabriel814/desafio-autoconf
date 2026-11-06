"""Ficha do lead: extração a cada mensagem e checklist de qualificação (regra em código).

O LLM só extrai o que o cliente disse. Quem decide o que já foi respondido e qual é a próxima
pergunta é o código, a partir da ficha. Assim o agente não volta em pergunta já respondida.
"""

import logging
import re
from typing import Literal

from pydantic import BaseModel, Field

from app import llm
from app.agente import prompts
from app.db import repo

log = logging.getLogger(__name__)

Campo = Literal["interesse", "faixa_preco", "pagamento", "visita", "carro_venda", "entrada", "troca", "urgencia",
                "idade", "faixa_renda"]

Motivo = Literal["reclamacao", "negociacao", "pedido_credito", "urgencia", "pediu_humano", "sem_resposta"]


class Extracao(BaseModel):
    """O que a ÚLTIMA mensagem do cliente informou. Campos não mencionados ficam nulos."""

    nome: str | None = None
    intencao: Literal["comprar", "vender"] | None = Field(
        None, description="vender = quer vender o carro dele para a loja (sem falar em comprar outro)")
    visita: str | None = Field(None, description="dia e/ou período em que pode ir à loja: 'sábado de manhã'")
    tipo_interesse: Literal["SUV", "Sedã", "Hatch", "Picape"] | None = None
    modelos_interesse: list[str] | None = None
    uso: str | None = Field(None, description="para que vai usar: cidade, estrada, família, trabalho...")
    preco_min: int | None = None
    preco_max: int | None = Field(None, description="orçamento máximo em reais inteiros (120 mil = 120000)")
    quer_financiar: bool | None = Field(None, description="true = financiar, false = à vista")
    entrada: int | None = Field(None, description="valor de entrada em reais inteiros")
    troca_tem: bool | None = Field(None, description="true se tem carro para troca, false se disse que não tem")
    troca_modelo: str | None = None
    troca_ano: int | None = None
    urgencia: Literal["baixa", "media", "alta"] | None = None
    primeiro_carro: bool | None = None
    idade: int | None = None
    faixa_renda: str | None = None
    sentimento: Literal["positivo", "neutro", "insatisfeito"] | None = None
    proximo_passo: str | None = Field(None, description="o que o cliente aceitou: visita, test drive, contato")
    nao_sabe: list[Campo] = Field(
        default_factory=list,
        description="itens que o cliente disse não saber ou não quis informar",
    )
    transferir: Motivo | None = Field(
        None, description="motivo para passar a conversa a um vendedor AGORA, se a mensagem trouxer um",
    )


def aplicar(lead_id: int, ext: Extracao, texto: str | None = None) -> dict:
    """Grava a extração na ficha sem apagar o que já existia.

    Regras em código (não dependem do LLM):
    - carro de troca com modelo/ano => troca_tem = True, e esse modelo não é "interesse";
    - entrada => quer financiar;
    - "não sei" só vale para o item que estava sendo perguntado (o próximo pendente do checklist).
    """
    ficha = repo.obter_lead(lead_id).ficha or {}
    campos = ext.model_dump(exclude={"nao_sabe", "transferir"}, exclude_none=True)
    t = texto or ""
    # Valor só se a mensagem tiver um valor: o LLM às vezes copia o preço de um carro da conversa ("pagar a vista")
    if texto is not None and not re.search(r"\d|\bmil\b|conto|pila|reais|cem|duzentos|trezentos", t, re.IGNORECASE):
        campos.pop("preco_max", None)
        campos.pop("preco_min", None)
        ext.preco_max = ext.preco_min = None  # também não conta como "interesse novo" (não mostra lista)
    # Quem está vendendo e responde o carro ("É um Gol 2018") está dizendo o carro DELE, não o que quer comprar
    vendendo = ficha.get("intencao") == "vender" and campos.get("intencao") != "comprar"
    if (vendendo and campos.get("modelos_interesse") and not campos.get("tipo_interesse")
            and not campos.get("preco_max") and not re.search(r"compr|procur|interess|quero (ver|um|uma)", t, re.I)):
        campos.setdefault("troca_modelo", campos.pop("modelos_interesse")[0])
        ano = re.search(r"\b(19[89]\d|20[0-4]\d)\b", t)
        if ano and not campos.get("troca_ano"):
            campos["troca_ano"] = int(ano.group(1))
        campos["troca_tem"] = True
    if campos.get("troca_modelo") or campos.get("troca_ano"):
        campos["troca_tem"] = True
        troca = (campos.get("troca_modelo") or "").lower()
        if troca and campos.get("modelos_interesse"):
            campos["modelos_interesse"] = [m for m in campos["modelos_interesse"]
                                           if m.lower() not in troca and troca not in m.lower()]
            if not campos["modelos_interesse"]:
                campos.pop("modelos_interesse")
    # "Não tem troca" só vale se ele disser isso: o LLM às vezes marca false com o carro da troca já anotado
    if (campos.get("troca_tem") is False and (ficha.get("troca_modelo") or campos.get("troca_modelo"))
            and not re.search(r"\bn[aã]o\b|\bsem\b", texto or "", re.IGNORECASE)):
        campos.pop("troca_tem")
    if campos.get("entrada"):
        campos["quer_financiar"] = True
    # Quem diz o carro que quer comprar está comprando (mesmo que antes tenha falado em vender o dele)
    if campos.get("tipo_interesse") or campos.get("modelos_interesse") or campos.get("preco_max"):
        campos["intencao"] = "comprar"
    falta = pendentes(ficha)
    duvida = texto is None or bool(_DUVIDA.search(texto))  # sem texto (testes): confia na extração
    if ext.nao_sabe and duvida and falta and falta[0] in ext.nao_sabe:
        campos["nao_informado"] = sorted(set(ficha.get("nao_informado", [])) | {falta[0]})
    if not campos:
        return ficha
    return repo.atualizar_ficha(lead_id, campos)


def mudou_interesse(antes: dict, ext: Extracao) -> bool:
    """A mensagem trouxe interesse ou orçamento NOVO (não só repetiu o que já estava na ficha)?"""
    if ext.tipo_interesse and ext.tipo_interesse != antes.get("tipo_interesse"):
        return True
    if ext.preco_max and ext.preco_max != antes.get("preco_max"):
        return True
    if ext.preco_min and ext.preco_min != antes.get("preco_min"):
        return True
    ja = {m.lower().strip() for m in antes.get("modelos_interesse") or []}
    # "Nissan Kicks" e "Kicks" são o mesmo carro: só conta como novo se não bate com nenhum já citado
    return any(not any(m.lower().strip() in j or j in m.lower().strip() for j in ja) for m in ext.modelos_interesse or [])


def extrair(texto_cliente: str, contexto: str, callbacks: list | None = None) -> Extracao:
    try:
        modelo = llm.chat(temperature=0).with_structured_output(Extracao)
        return modelo.invoke(
            prompts.EXTRACAO.format(contexto=contexto or "(início da conversa)", mensagem=texto_cliente),
            config={"callbacks": callbacks or [], "run_name": "extrair_dados"},
        )
    except llm.SemChaveOpenAI:
        raise
    except Exception:
        log.exception("Extração falhou, seguindo sem atualizar a ficha")
        return Extracao()


# ---------- Checklist ----------

# (campo, rótulo, como perguntar, preenchido?) na ordem em que o agente deve perguntar
def _itens(f: dict) -> list[tuple[str, str, str, object]]:
    reais = lambda v: f"R$ {v:,}".replace(",", ".")  # noqa: E731
    faixa = None
    if f.get("preco_max") or f.get("preco_min"):
        faixa = " ".join(
            p for p in (
                f"de {reais(f['preco_min'])}" if f.get("preco_min") else "",
                f"até {reais(f['preco_max'])}" if f.get("preco_max") else "",
            ) if p
        )
    # Cada item: (campo, rótulo, como perguntar, valor completo ou None, parcial: o que já se sabe)
    # Item parcial = o cliente respondeu só uma parte; a próxima pergunta é a parte que falta.

    # Interesse: tipo ou modelo. Só o uso ("pra família") é parcial: ainda falta o tipo de carro.
    tipo_ou_modelo = f.get("tipo_interesse") or ", ".join(f.get("modelos_interesse") or [])
    if tipo_ou_modelo:
        interesse = (tipo_ou_modelo, None, "")
    elif f.get("uso"):
        interesse = (None, f"uso: {f['uso']} (falta o tipo de carro)",
                     f"que tipo de carro combina com esse uso ({f['uso']}): SUV, sedã, hatch ou picape")
    else:
        interesse = (None, None, "que tipo de carro procura ou para que vai usar")

    pagamento = None if f.get("quer_financiar") is None else ("financiar" if f["quer_financiar"] else "à vista")

    # Troca: só está completa com modelo E ano (ou se o cliente disse que não tem carro para troca).
    modelo, ano = f.get("troca_modelo"), f.get("troca_ano")
    if f.get("troca_tem") is False:
        troca = ("não tem", None, "")
    elif f.get("troca_tem") and modelo and ano:
        troca = (f"{modelo} {ano}", None, "")
    elif f.get("troca_tem") and modelo:
        troca = (None, f"{modelo} (falta o ano)", f"de que ano é o {modelo} que ele vai dar na troca")
    elif f.get("troca_tem") and ano:
        troca = (None, f"ano {ano} (falta o modelo)", "qual é o modelo do carro que ele vai dar na troca")
    elif f.get("troca_tem"):
        troca = (None, "tem carro (falta modelo e ano)", "qual é o carro da troca: modelo e ano")
    else:
        troca = (None, None, "se tem carro para dar na troca (modelo e ano)")

    if f.get("intencao") == "vender":
        # Quer vender o carro dele para a loja: qual é o carro e quando traz para avaliar.
        if modelo and ano:
            carro = (f"{modelo} {ano}", None, "")
        elif modelo:
            carro = (None, f"{modelo} (falta o ano)", f"de que ano é o {modelo} dele")
        elif ano:
            carro = (None, f"ano {ano} (falta o modelo)", "qual é o modelo do carro dele")
        else:
            carro = (None, None, "qual é o carro que ele quer vender (modelo e ano)")
        return [
            ("carro_venda", "Carro que quer vender", carro[2], carro[0], carro[1]),
            ("visita", "Visita para avaliação",
             "que dia e período ele consegue trazer o carro na loja para a avaliação", f.get("visita"), None),
        ]

    # Compra: valor, modelo, como quer comprar e, por fim, o dia da visita para ver o carro.
    # O valor vem primeiro: com ele já dá para listar tudo o que cabe e o cliente escolhe o carro vendo a lista.
    itens = [
        ("faixa_preco", "Valor", "até quanto pensa em investir", faixa, None),
        ("interesse", "Modelo / tipo de carro", interesse[2], interesse[0], interesse[1]),
        ("pagamento", "Como quer comprar", "se pensa em pagar à vista ou financiar", pagamento, None),
    ]
    # Complemento (não é pergunta fixa): o cliente CITOU um carro de troca (modelo ou ano) mas faltou a outra parte.
    # Um "tenho troca" vago não segura a qualificação.
    if f.get("troca_tem") and troca[0] is None and (modelo or ano):
        itens.append(("troca", "Carro na troca", troca[2], None, troca[1]))
    itens.append(("visita", "Visita na loja",
                  "que dia e período ele consegue vir na loja ver o carro de perto", f.get("visita"), None))
    return itens


# O que o cliente contar além das 3 perguntas vai para a ficha e para o resumo, mas não é perguntado.
EXTRAS = [("troca", "Carro na troca"), ("entrada", "Entrada"), ("urgencia", "Urgência"),
          ("idade", "Idade"), ("faixa_renda", "Faixa de renda")]


def _extras_texto(f: dict) -> list[str]:
    linhas = []
    if f.get("intencao") == "vender":
        pass  # o carro dele é o carro à venda, não troca
    elif f.get("troca_tem") is False:
        linhas.append("Carro na troca: não tem")
    elif f.get("troca_modelo") and f.get("troca_ano"):
        linhas.append(f"Carro na troca: {f['troca_modelo']} {f['troca_ano']}")
    if f.get("entrada"):
        linhas.append(f"Entrada: R$ {f['entrada']:,}".replace(",", "."))
    for k, rot in (("urgencia", "Urgência"), ("idade", "Idade"), ("faixa_renda", "Faixa de renda")):
        if f.get(k):
            linhas.append(f"{rot}: {f[k]}")
    return linhas


MAX_PERGUNTAS = 2  # depois de perguntar 2 vezes sem resposta, o item é deixado de lado (sem laço)


def _desistidos(ficha: dict) -> set[str]:
    return {k for k, n in (ficha.get("perguntas_feitas") or {}).items() if n >= MAX_PERGUNTAS}


def pendentes(ficha: dict) -> list[str]:
    pular = set(ficha.get("nao_informado") or []) | _desistidos(ficha)
    return [campo for campo, _, _, valor, _ in _itens(ficha) if not valor and campo not in pular]


def situacao_campos(ficha: dict) -> list[dict]:
    """Cada informação da ficha com o que aconteceu com ela: respondido, não perguntado, sem resposta, não soube.

    Usado na tela da ficha e no resumo para o vendedor (para ele saber o que ainda precisa levantar).
    """
    f = ficha or {}
    reais = lambda v: f"R$ {v:,}".replace(",", ".")  # noqa: E731
    feitas = f.get("perguntas_feitas") or {}
    nao_sabe = set(f.get("nao_informado") or [])

    def principal(campo: str, valor) -> tuple[str, str]:
        if valor:
            return valor, "respondido"
        if campo in nao_sabe:
            return "cliente não soube", "nao_soube"
        if feitas.get(campo):
            return "perguntado, sem resposta", "sem_resposta"
        return "ainda não perguntado", "nao_perguntado"

    def extra(valor) -> tuple[str, str]:
        return (valor, "respondido") if valor else ("não perguntado", "nao_perguntado")

    modelo = f.get("tipo_interesse") or ""
    if f.get("modelos_interesse"):
        modelo = (modelo + " · " if modelo else "") + ", ".join(f["modelos_interesse"])
    if not modelo and f.get("uso"):
        modelo = ""
    faixa = " ".join(p for p in (f"de {reais(f['preco_min'])}" if f.get("preco_min") else "",
                                 f"até {reais(f['preco_max'])}" if f.get("preco_max") else "") if p)
    pagamento = None if f.get("quer_financiar") is None else ("financiado" if f["quer_financiar"] else "à vista")
    if f.get("troca_tem") is False:
        troca = "não tem"
    elif f.get("troca_tem"):
        troca = " ".join(str(x) for x in (f.get("troca_modelo") or "sim", f.get("troca_ano") or "(ano não informado)"))
    else:
        troca = None
    primeiro = None if f.get("primeiro_carro") is None else ("sim" if f["primeiro_carro"] else "não")

    if f.get("intencao") == "vender":
        carro = " ".join(str(x) for x in (f.get("troca_modelo"), f.get("troca_ano")) if x)
        topo = [
            ("Intenção", "vender o carro para a loja", "respondido"),
            ("Carro que quer vender", *principal("carro_venda", carro)),
            ("Visita para avaliação", *principal("visita", f.get("visita"))),
        ]
    else:
        topo = [
            ("Modelo / tipo de carro", *principal("interesse", modelo)),
            ("Valor", *principal("faixa_preco", faixa)),
            ("Como quer comprar", *principal("pagamento", pagamento)),
            ("Visita na loja", *principal("visita", f.get("visita"))),
        ]
    linhas = [
        *topo,
        ("Uso", *extra(f.get("uso"))),
        ("Carro na troca", *extra(troca if f.get("intencao") != "vender" else None)),
        ("Entrada", *extra(f.get("entrada") and reais(f["entrada"]))),
        ("Urgência", *extra(f.get("urgencia"))),
        ("Primeiro carro", *extra(primeiro)),
        ("Idade", *extra(f.get("idade"))),
        ("Faixa de renda", *extra(f.get("faixa_renda"))),
        ("Próximo passo", *extra(f.get("proximo_passo"))),
    ]
    return [{"rotulo": r, "valor": str(v), "situacao": s} for r, v, s in linhas]


def ficha_para_resumo(ficha: dict) -> str:
    return "\n".join(f"- {c['rotulo']}: {c['valor']}" for c in situacao_campos(ficha))


def proxima_pergunta(ficha: dict) -> str | None:
    """O texto exato da próxima pergunta do checklist (ou None se já está qualificado)."""
    falta = pendentes(ficha)
    if not falta:
        return None
    return next(como for campo, _, como, _, _ in _itens(ficha) if campo == falta[0])


def registrar_pergunta(lead_id: int, ficha_antes: dict) -> None:
    """Conta quantas vezes o item pendente já foi perguntado (o agente pergunta o próximo do checklist)."""
    falta = pendentes(ficha_antes)
    if not falta:
        return
    contagem = dict(ficha_antes.get("perguntas_feitas") or {})
    contagem[falta[0]] = contagem.get(falta[0], 0) + 1
    repo.atualizar_ficha(lead_id, {"perguntas_feitas": contagem})


def checklist(ficha: dict) -> str:
    """Texto que vai no prompt do agente a cada turno."""
    ficha = ficha or {}
    nao = set(ficha.get("nao_informado") or [])
    desistidos = _desistidos(ficha)
    falta = pendentes(ficha)
    proxima = falta[0] if falta else None
    linhas = []
    for campo, rotulo, como, valor, parcial in _itens(ficha):
        if valor:
            linhas.append(f"✅ {rotulo}: {valor}")
        elif campo in nao:
            linhas.append(f"✅ {rotulo}: cliente não soube ou não quis informar (não pergunte de novo)")
        elif campo in desistidos:
            sabido = f" ({parcial})" if parcial else ""
            linhas.append(f"✅ {rotulo}{sabido}: já perguntado {MAX_PERGUNTAS} vezes sem resposta (não pergunte de novo)")
        else:
            marca = f"🟡 {rotulo}: {parcial}" if parcial else f"⬜ {rotulo}"
            if campo == proxima:
                extra = " (não repita a parte que ele já disse)" if parcial else ""
                linhas.append(f"{marca}  ← PRÓXIMA PERGUNTA: pergunte {como}{extra}")
            else:
                linhas.append(marca)
    extras = _extras_texto(ficha)
    if extras:
        linhas.append("Também já sabemos (não pergunte, só use se ajudar): " + " · ".join(extras))
    if not falta and qualificado(ficha):
        linhas.append("✅ Checklist completo: visita combinada, lead QUALIFICADO (o sistema passa para um vendedor "
                      "confirmar o horário)")
    elif not falta:
        # Perguntou tudo, mas faltou resposta: não é "já temos tudo"
        linhas.append("Alguma pergunta ficou sem resposta: NÃO diga que já tem tudo e NÃO ofereça vendedor. "
                      "Continue ajudando o cliente com o que ele trouxer.")
    elif proxima == "visita":
        linhas.append("Agora é hora de convidar para vir à loja: pergunte o dia e o período. Não diga que o horário "
                      "está garantido: o vendedor confirma depois.")
    else:
        linhas.append("Ainda faltam itens: NÃO ofereça vendedor, visita nem test drive por enquanto. Faça SÓ a "
                      "PRÓXIMA PERGUNTA, uma por mensagem: não junte com os outros itens ⬜ (eles ficam para depois).")
    return "\n".join(linhas)


def qualificado(ficha: dict) -> bool:
    """Lead qualificado = intenção clara até a visita marcada, com RESPOSTAS de verdade.

    Compra: modelo, valor, como quer comprar e o dia da visita. Venda: o carro dele (modelo e ano) e o dia da visita.
    "Não sei" ou "perguntado 2 vezes sem resposta" fazem o agente seguir em frente, mas não qualificam.
    """
    ficha = ficha or {}
    tem_visita = bool(ficha.get("visita"))
    if ficha.get("intencao") == "vender":
        return bool(ficha.get("troca_modelo") and ficha.get("troca_ano")) and tem_visita and not pendentes(ficha)
    tem_modelo = bool(ficha.get("tipo_interesse") or ficha.get("modelos_interesse"))
    tem_valor = bool(ficha.get("preco_max") or ficha.get("preco_min"))
    tem_pagamento = ficha.get("quer_financiar") is not None
    return tem_modelo and tem_valor and tem_pagamento and tem_visita and not pendentes(ficha)


# "Não sei" só vale se a mensagem mostrar dúvida de verdade (o LLM às vezes marca sem o cliente dizer)
_DUVIDA = re.compile(
    r"n[aã]o sei|ainda n[aã]o (sei|decidi|pensei)|n[aã]o decidi|tanto faz|depende|sei l[aá]|n[aã]o tenho ideia"
    r"|sem ideia|n[aã]o fa[cç]o ideia|t[oô] vendo ainda|vou ver ainda|qualquer um|tanto o faz",
    re.IGNORECASE,
)


# Rede de segurança em código: sinais fortes na mensagem do cliente.
# Vale quando a extração (LLM) não detectar o motivo. A ordem importa: o primeiro que bate ganha.
_SINAIS = [
    ("reclamacao", r"procon|reclama|absurd|descaso|ningu[eé]m (me )?respond|terceira vez|"
                   r"deu (problema|defeito)|veio com (problema|defeito)|processar|advogado|p[eé]ssimo atendimento"),
    ("negociacao", r"desconto|abaixa(r)? (o|esse|um pouco)|faz(er)? por|fecho (se|por|no|na|com)|se fizer|"
                   r"melhor pre[cç]o|contraproposta|fa[cç]o (uma )?proposta|chorar|negoci|"
                   # quanto a loja paga no carro do cliente (troca ou venda): só o vendedor avalia
                   r"quanto (voc[eê]s |vcs |a loja )?(paga|pagam|pagaria|pagariam|d[aã]o|daria|dariam|avalia|oferece)"
                   r"|(paga|pagam|pagaria|pagariam|d[aã]o|daria|dariam|oferece|oferecem) quanto"
                   r"|quanto (vale|valeria|sai|fica) (o |a )?(meu|minha)|valor (do|da) (meu|minha)|"
                   r"avalia(r|[cç][aã]o)? (do |da |o |a )?(meu|minha)|(meu|minha) \w+ (na|pela) (fipe|tabela)"),
    # "parcelado"/"parcelar" é só querer financiar; pedido de crédito é perguntar a parcela, juros, aprovação...
    ("pedido_credito", r"\bparcelas?\b|presta[cç][aã]o|juros|taxa|simula[cç]|nome (t[aá] |est[aá] )?sujo|negativad|"
                       r"score|aprova(m|r|do|[cç][aã]o)|\d+\s*(x|vezes)\b"),
    ("urgencia", r"quebrou|urgente|urg[eê]ncia|essa semana|esta semana|at[eé] (segunda|ter[cç]a|quarta|quinta|"
                 r"sexta|s[aá]bado|domingo)|pra ontem|o quanto antes|preciso (dele |de um carro )?(hoje|amanh[aã])"),
    ("pediu_humano", r"(falar|conversar) com (um|uma|o|a|algum|alguma) (pessoa|atendente|humano|vendedor|gerente)"
                     r"|atendente (de verdade|humano)|pessoa (de verdade|real)|quero um vendedor|chama (um|o) vendedor"
                     r"|passa(r)? (pra|para) (um |uma |o |a )?(vendedor|atendente|pessoa|humano|algu[eé]m da (equipe|loja))"
                     r"|algu[eé]m (da loja|da equipe|de verdade)|me liga(m)?\b|quero (um )?humano"),
    # Perguntas que o estoque (marca, modelo, ano, km, preço, tipo) não responde: a Lia não inventa, chama o vendedor
    ("sem_resposta", r"garantia|laudo|cautelar|leil[aã]o|sinistr|(foi|j[aá] foi|é) batid|[uú]nico dono"
                     r"|multas?\b|ipva|licenciamento|revis(ão|ao|ões|oes)|manual e chave|chave reserva"
                     r"|aceita(m)? (moto|outro carro|carro e moto)|qual (o )?endere[cç]o|onde fica (a loja|voc[eê]s)"
                     r"|hor[aá]rio de (funcionamento|atendimento)|abre (no|de|aos) (s[aá]bado|domingo|feriado)"),
]


def motivo_por_regra(texto: str) -> str | None:
    import re

    t = (texto or "").lower()
    for motivo, padrao in _SINAIS:
        if re.search(padrao, t):
            return motivo
    return None


ROTULO_MOTIVO = {
    "reclamacao": "reclamação ou insatisfação",
    "negociacao": "negociação de valor ou pedido de desconto",
    "pedido_credito": "pedido de crédito, parcela ou simulação",
    "urgencia": "urgência alta",
    "pediu_humano": "cliente pediu para falar com uma pessoa",
    "sem_resposta": "uma pergunta que você não sabe responder com certeza (não está no estoque)",
}


def acao_obrigatoria(motivo: str, ficha: dict | None = None, com_lista: bool = False) -> str:
    """Substitui o checklist quando a conversa precisa ir para um vendedor."""
    if motivo == "qualificado":
        f = ficha or {}
        sem_lista = "" if com_lista else "não mostre carros e não chame buscar_estoque. "
        visita = f.get("visita") or "o dia que ele disse"
        para = "trazer o carro para a avaliação" if f.get("intencao") == "vender" else "ver o carro"
        return (
            f"⚠️ AÇÃO OBRIGATÓRIA: a visita está combinada ({visita}, para {para}), o lead está QUALIFICADO. Chame "
            "transferir_humano com motivo \"qualificado\" AGORA, antes de responder. Depois diga ao cliente que "
            f"anotou {visita} e que o vendedor vai chamar por aqui para confirmar o horário. Não faça mais perguntas; "
            f"{sem_lista}"
            "Não diga que o horário ou o carro estão garantidos. Nunca prometa crédito aprovado, taxa ou parcela. "
            "NUNCA diga ao cliente que ele está \"qualificado\" nem use termos internos (lead, checklist, "
            f"transferência): fale como gente, ex.: \"Fechado, anotei aqui {visita}! O vendedor vai te chamar por "
            "aqui pra confirmar o horário certinho.\""
        )
    return (
        f"⚠️ AÇÃO OBRIGATÓRIA: a última mensagem é {ROTULO_MOTIVO[motivo]}. Chame transferir_humano "
        f"com motivo \"{motivo}\" AGORA, antes de responder. Não faça perguntas de qualificação.\n"
        f"Como responder, de forma humana: {COMO_PASSAR[motivo]} Nunca diga \"não posso\" nem \"não tenho "
        "autorização\": diga o que o vendedor vai fazer POR ele. Curto, caloroso, sem frase pronta."
    )


# Como passar para o vendedor sem soar como procedimento: reconhecer o pedido e dizer o que vai acontecer.
COMO_PASSAR = {
    "reclamacao": "peça desculpas de verdade pelo transtorno, mostre que entendeu o problema dele (cite o que ele "
                  "contou) e diga que uma pessoa da equipe vai assumir agora para resolver. Sem emoji e sem "
                  "oferecer carro.",
    "negociacao": "se ele propôs valor ou pediu desconto, mostre que gostou de ele querer fechar (é uma boa "
                  "notícia!), cite o carro e o valor e diga que o vendedor é quem consegue ver as condições com ele e "
                  "já vai chamar por aqui. Se ele perguntou quanto a loja paga no carro DELE, diga que quem avalia é o "
                  "vendedor e que ele já vai chamar por aqui. Não aceite, não recuse e não estime nenhum valor.",
    "sem_resposta": "diga, com naturalidade, que essa informação você prefere confirmar com o vendedor pra não "
                    "passar nada errado, e que ele já vai responder por aqui. Não invente nem chute a resposta.",
    "pedido_credito": "diga que a loja trabalha com financiamento e que o vendedor vai fazer a simulação certinha "
                      "com ele, já já, por aqui. Não prometa aprovação, taxa nem parcela.",
    "urgencia": "mostre empatia com a pressa (cite o motivo, se ele disse), diga que vai acelerar e que o vendedor "
                "já vai falar com ele por aqui pra resolver rápido.",
    "pediu_humano": "responda que claro, sem problema nenhum, e que já chamou um vendedor para continuar com ele "
                    "por aqui.",
}
