"""Etapa 05: agente em LangGraph.

extrair_dados -> carregar_contexto (ficha + checklist) -> agente (LLM) <-> tools -> checar_transferencia -> resposta
                                                  \\-> (cobra o agente 1x se prometeu transferir sem a tool)
"""

import re
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from app import llm
from app.config import settings
from app.agente import ficha as ficha_mod
from app.agente.prompts import fixa, prompt_sistema
from app.agente.tools import criar_tools
from app.db import repo


class Estado(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    lead_id: int
    cobrado: bool
    transferir: str | None  # motivo detectado na extração (reclamação, negociação, ...)
    mostrar_carros: bool  # a mensagem trouxe interesse ou faixa de preço nova
    modelos_agora: list[str]  # modelos citados nesta mensagem
    cobrado_cedo: bool  # já foi cobrado por oferecer visita antes da hora
    cobrado_estilo: bool  # já reescreveu uma vez por soar como robô
    cobrado_honestidade: bool  # já reescreveu uma vez por não dizer que é assistente virtual
    escolha_respondida: bool  # respondeu o "gostou de algum?" depois da lista
    cobrado_promessa: bool  # já reescreveu uma vez por prometer vendedor sem poder transferir


def _linha_carro(c: dict) -> str:
    km = f"{c['km']:,}".replace(",", ".")
    preco = f"{c['preco']:,}".replace(",", ".")
    return f"- {c['nome']} {c['ano']} · {km} km · R$ {preco}"


FECHO_SUGESTAO = (
    "Mostre TODOS os carros desta lista, um por linha, sem cortar nenhum. "
    "Depois deles, faça a próxima pergunta do checklist. Não pergunte o que já está ✅."
)
MAX_CARROS = 10  # teto por mensagem (WhatsApp); o estoque inteiro de um tipo cabe aqui


PERGUNTA_ESCOLHA = "se ele gostou de algum desses carros (do jeito que se fala, ex.: \"Gostou de algum?\")"


def perguntar_escolha(ficha: dict) -> bool:
    """Depois da lista, perguntar se gostou de algum ANTES do próximo item do checklist.

    Só quando a lista já leva em conta o valor (se o valor ainda falta, ele é a próxima pergunta: estreita a lista),
    o cliente ainda não escolheu um modelo e ainda falta alguma pergunta (qualificado vai direto para o vendedor).
    """
    falta = ficha_mod.pendentes(ficha)
    # parte faltando de algo que ele já contou (ex.: o ano da troca) vem antes: senão a resposta "É 2017" fica solta
    return bool(falta) and falta[0] in ("interesse", "pagamento", "visita") and not ficha.get("modelos_interesse")


def _fecho(ficha: dict, ja_comentado: list[str] | None = None, unico: bool = False) -> str:
    """Depois da lista: a pergunta EXATA que vem a seguir (calculada pelo código, não escolhida pelo agente).

    `unico`: a lista tem um carro só. Aí não há com quem comparar ("o mais novinho" não faz sentido):
    o agente avisa, de um jeito humano, que é o único que a loja tem com aquele pedido.
    """
    prox = PERGUNTA_ESCOLHA if perguntar_escolha(ficha) else ficha_mod.proxima_pergunta(ficha)
    if unico:
        base = (
            "Como montar a mensagem: 1) uma frase curta reagindo ao que o cliente acabou de dizer, sem \"Aqui "
            "estão\"; 2) o carro da lista numa FRASE corrida, não numa lista nem com traço no começo, dizendo o "
            "nome, o ano, os km e o preço do jeito que está na lista (ex.: \"Temos o BYD Dolphin 2024, com 5 km, "
            "por R$ 89.000\"); 3) diga, de um jeito natural e simpático, "
            "como um vendedor falaria, com as suas próprias palavras e junto de um comentário sobre o carro, que "
            "esse é o ÚNICO que a loja tem agora com o que ele pediu (ideias: \"é o único que chegou por aqui\", "
            "\"por enquanto só ele no pátio\", \"tenho só esse na loja\"; não copie estas frases). Sem tom de "
            "aviso de sistema, e só uma vez: se você já disse isso antes nesta conversa, não repita. Comente só o "
            "carro em si (ano, km), sem comparar com outros: NÃO diga \"o mais novinho\", \"o mais em conta\", "
            "\"o mais barato\" nem nenhum superlativo, porque não existe outro para comparar. "
        )
    else:
        base = (
            "Como montar a mensagem: 1) uma frase curta reagindo ao que o cliente acabou de dizer (o tipo, o "
            "orçamento, a família, a troca...), sem \"Aqui estão\"; 2) TODOS os carros desta lista, NA MESMA ORDEM "
            "(do mais barato ao mais caro), um por linha, sem cortar nenhum e sem negrito; 3) um comentário curto "
            "de vendedor sobre 1 ou 2 deles, só com os dados da lista (mais novo, menos km, mais em conta, mais "
            "perto do orçamento). "
        )
    if ja_comentado:
        base += ("Você JÁ comentou nesta conversa: " + " | ".join(f'"{c}"' for c in ja_comentado)
                 + ". Não repita esses comentários nem a mesma ideia: destaque outro carro ou outro ponto, "
                   "ou não comente nada. ")
    if prox:
        return base + f"No fim da mensagem, pergunte SÓ isto: {prox}. Não pergunte mais nada."
    return base


def destaques(carros: list[dict]) -> str:
    """Os destaques VERDADEIROS da lista, calculados aqui (o agente errava "o mais novinho" de cabeça)."""
    if len(carros) < 2:
        return ""
    novo = max(carros, key=lambda c: (c["ano"], -c["km"]))
    menos_km = min(carros, key=lambda c: c["km"])
    barato = min(carros, key=lambda c: c["preco"])
    km = lambda c: f"{c['km']:,}".replace(",", ".")  # noqa: E731
    itens = [f"mais novo: {novo['nome']} ({novo['ano']})",
             f"menos rodado: {menos_km['nome']} ({km(menos_km)} km)",
             f"mais em conta: {barato['nome']} (R$ {barato['preco']:,})".replace(",", ".")]
    return ("Destaques VERDADEIROS desta lista (se for comentar, use só estes, não invente outro): "
            + " · ".join(itens) + ".")


def comentarios_feitos(lead_id: int) -> list[str]:
    """Comentários de vendedor que o agente já fez (frases que citam carro mas não são linha da lista nem pergunta)."""
    feitos = []
    for m in repo.historico(lead_id, 12):
        if m.papel != "agente":
            continue
        for frase in re.split(r"(?<=[.!?])\s+|\n", m.texto):
            f = frase.strip()
            if (f and not f.startswith(("-", "•")) and not f.endswith("?") and "R$" not in f
                    and re.search(r"\b(km|novinho|em conta|econ[oô]mic|barat|mais novo|menos)\b", f, re.I)):
                feitos.append(f[:120])
    return feitos[-3:]


def _ja_citado(carro: dict, textos: list[str]) -> bool:
    """O carro (nome e preço) já apareceu numa das mensagens do agente."""
    preco = f"{carro['preco']:,}".replace(",", ".")
    return any(carro["nome"] in t and preco in t for t in textos)


def textos_do_agente(lead_id: int, n: int = 2) -> list[str]:
    """As últimas mensagens do agente (para não mandar de novo a lista que ele acabou de mandar)."""
    return [m.texto for m in repo.historico(lead_id, 8) if m.papel == "agente"][-n:]


def sugestoes(ficha: dict, modelos_agora: list[str] | None = None, ja_comentado: list[str] | None = None,
              mostrados: list[str] | None = None) -> str:
    """O código escolhe os carros a mostrar; o agente só apresenta.

    `modelos_agora`: modelos citados NESTA mensagem (os antigos da ficha não contam).
    `ja_comentado`: comentários que o agente já fez (para não repetir).
    `mostrados`: últimas mensagens do agente; se os carros desta lista já estão nelas, não se lista de novo.
    """
    def fecho(carros: list[dict]) -> str:
        return _fecho(ficha, ja_comentado, unico=len(carros) == 1)

    def retoma(carros: list[dict]) -> str | None:
        """O carro único da lista já foi mostrado na mensagem anterior: retoma em palavras, sem repetir a linha."""
        if len(carros) != 1 or not mostrados or not _ja_citado(carros[0], mostrados):
            return None
        prox = PERGUNTA_ESCOLHA if perguntar_escolha(ficha) else ficha_mod.proxima_pergunta(ficha)
        retomar = (f"diga, de um jeito natural, que o {carros[0]['nome']} que você acabou de mostrar é o único "
                   "que a loja tem com o que ele pediu e que ele cabe no valor dele")
        return (f"Você JÁ mostrou o {carros[0]['nome']} na mensagem anterior. NÃO liste de novo, "
                "não repita a linha com km e preço e não fale em \"te mando a lista\". Como montar a mensagem: "
                f"1) uma frase curta reagindo ao que o cliente acabou de dizer; 2) {retomar}, sem superlativos "
                "(\"o mais novinho\") e sem repetir comentário que já fez. "
                + (f"No fim da mensagem, pergunte SÓ isto: {prox}. Não pergunte mais nada." if prox else ""))

    tipo, pmax, pmin = ficha.get("tipo_interesse"), ficha.get("preco_max"), ficha.get("preco_min")
    modelos = modelos_agora or []
    achados, faltando = [], []
    for m in modelos:
        r = repo.buscar_carros(texto=m)
        if r:
            achados += [c for c in r if c not in achados]
        else:
            faltando.append(m)

    if achados:
        if (r := retoma(achados[:MAX_CARROS])):
            return r
        lista ="\n".join(_linha_carro(c) for c in achados[:MAX_CARROS])
        return (f"MOSTRE AGORA, nesta mensagem, o(s) carro(s) que o cliente pediu:\n{lista}\n"
                f"{destaques(achados[:MAX_CARROS])}\n{fecho(achados[:MAX_CARROS])}")

    # O cliente já pediu um carro ou marca (ex.: "um BYD") e agora só afinou o valor: a lista continua sendo DELE,
    # não de todo o estoque até aquele preço
    if not modelos and (pmax or pmin):
        do_interesse: list[dict] = []
        for m in ficha.get("modelos_interesse") or []:
            do_interesse += [c for c in repo.buscar_carros(texto=m, tipo=tipo) if c not in do_interesse]
        if do_interesse:
            cabem = [c for c in do_interesse if (not pmax or c["preco"] <= pmax) and (not pmin or c["preco"] >= pmin)]
            if cabem:
                if (r := retoma(sorted(cabem, key=lambda c: c["preco"])[:MAX_CARROS])):
                    return r
                lista ="\n".join(_linha_carro(c) for c in sorted(cabem, key=lambda c: c["preco"])[:MAX_CARROS])
                return (f"O cliente já pediu {', '.join(ficha['modelos_interesse'])} e agora disse o valor. MOSTRE AGORA, "
                        "nesta mensagem, só os carros que ele pediu e que cabem no valor (não ofereça outros modelos "
                        f"nem outras marcas):\n{lista}\n{destaques(cabem[:MAX_CARROS])}\n{fecho(cabem[:MAX_CARROS])}")
            if not tipo:  # tem o que ele quer no estoque, mas fora do valor: diz isso e mostra o mais perto
                alvo = pmax or pmin
                perto = sorted(sorted(do_interesse, key=lambda c: abs(c["preco"] - alvo))[:3], key=lambda c: c["preco"])
                lista = "\n".join(_linha_carro(c) for c in perto)
                return (f"Nenhum {', '.join(ficha['modelos_interesse'])} do estoque cabe nesse valor. Diga isso com "
                        f"clareza e mostre estes, que são os mais perto do valor dele:\n{lista}\n{fecho(perto)}")

    parecidos = repo.buscar_carros(tipo=tipo, preco_max=pmax, preco_min=pmin) if (tipo or pmax or pmin) else []
    parecidos.sort(key=lambda c: c["preco"])  # sempre do mais barato ao mais caro (o mais perto do teto fica no fim)
    achados.sort(key=lambda c: c["preco"])

    if faltando:
        # Sem o modelo, a faixa de preço é o que abre mais opções (o estoque de seminovos é pequeno)
        sem_valor = not (pmax or pmin)
        if not parecidos and not sem_valor:
            parecidos = repo.buscar_carros(preco_max=pmax, preco_min=pmin)  # outro tipo, mas cabe no orçamento
        if not parecidos and tipo:
            parecidos = repo.buscar_carros(tipo=tipo)
        parecidos.sort(key=lambda c: c["preco"])
        pedir_valor = (" No fim, pergunte SÓ isto: até quanto ele pensa em investir, para você mostrar mais opções "
                       "nessa faixa de preço." if sem_valor else "")
        if not parecidos:
            return (
                f"O cliente pediu {', '.join(faltando)}, que NÃO está no estoque. Diga isso com clareza e use "
                f"buscar_estoque para sugerir 1 ou 2 da mesma categoria, com ano e preço.{pedir_valor}"
            )
        if sem_valor:
            lista = "\n".join(_linha_carro(c) for c in parecidos[:2])
            return (
                f"O cliente pediu {', '.join(faltando)}, que NÃO está no estoque. Diga isso com clareza e, na MESMA "
                f"mensagem, sugira estes parecidos, um por linha (nome, ano, km e preço):\n{lista}\n{pedir_valor}"
            )
        lista = "\n".join(_linha_carro(c) for c in parecidos[:MAX_CARROS])
        return (
            f"O cliente pediu {', '.join(faltando)}, que NÃO está no estoque. Diga isso com clareza e, "
            f"na MESMA mensagem, ofereça TODOS estes, que cabem no que ele procura, um por linha (nome, ano, km e "
            f"preço), do mais barato ao mais caro:\n{lista}\n{destaques(parecidos[:MAX_CARROS])}\n{fecho(parecidos[:MAX_CARROS])}"
        )

    if parecidos:
        if (r := retoma(parecidos[:MAX_CARROS])):
            return r
        lista ="\n".join(_linha_carro(c) for c in parecidos[:MAX_CARROS])
        return (
            "O cliente acabou de dizer o que procura ou quanto pode pagar. MOSTRE AGORA, nesta mensagem, "
            f"estes carros do estoque (são os que combinam):\n{lista}\n{destaques(parecidos[:MAX_CARROS])}\n{fecho(parecidos[:MAX_CARROS])}"
        )

    # Nada combina: oferece o mais próximo do mesmo tipo, ou do orçamento
    proximos = repo.buscar_carros(tipo=tipo) if tipo else repo.buscar_carros()
    if not proximos:
        proximos = repo.buscar_carros()
    if pmax:
        proximos.sort(key=lambda c: abs(c["preco"] - pmax))
    proximos = sorted(proximos[:3], key=lambda c: c["preco"])  # os 3 mais próximos, em ordem de preço
    lista = "\n".join(_linha_carro(c) for c in proximos)
    return (
        "Nenhum carro do estoque bate exatamente com o que o cliente pediu. Diga isso com clareza e "
        f"sugira estes, que são os mais próximos do pedido:\n{lista}"
    )


# O agente prometeu passar para um vendedor?
_PROMETEU_TRANSFERIR = re.compile(
    r"transfer|passar (sua conversa|voc[eê]|o atendimento|pra|para)|vendedor (vai|ir[aá]|j[aá])"
    r"|cham(ar|o|ei) (um |o |nosso )?vendedor|(pessoa|algu[eé]m) da (nossa )?equipe|equipe (vai|ir[aá])|encaminh"
    r"|deix(ar|o) (um |o )?vendedor|vendedor (pronto|entrar|chamar|vai te)|um dos nossos vendedores",
    re.IGNORECASE,
)

_OFERECE_VISITA = re.compile(r"visita|test[\s-]?drive|agendar", re.IGNORECASE)
# Cliente quer saber se fala com robô/pessoa: a resposta tem que ser honesta (assistente virtual)
_PERGUNTA_ROBO = re.compile(
    r"\brob[oô]\b|\bbot\b|intelig[eê]ncia artificial|\bi\.?a\.?\b|[eé] (uma )?pessoa|[eé] humano|[eé] de verdade"
    r"|[eé] gente|t[oô] falando com (quem|algu[eé]m|uma pessoa|um rob)|voc[eê] [eé] real",
    re.IGNORECASE,
)
_DIZ_VIRTUAL = re.compile(r"assistente virtual|virtual|sou (uma |a )?(assistente|ia|intelig)", re.IGNORECASE)
HONESTIDADE = (
    "O cliente perguntou diretamente se está falando com um robô ou uma pessoa. Não minta: em UMA frase curta e "
    "leve, diga que você é a {atendente}, a assistente virtual da loja, e que um vendedor assume quando ele quiser. "
    "Sem se chamar de robô, sem explicar como funciona e sem textão; depois siga a conversa normalmente."
)

# Cliente pediu para VER carros ("quais SUVs vocês têm?", "me mostra", "o que tem de hatch?")
_PEDIU_LISTA = re.compile(
    r"\bquais\b|\bmostr|\bme manda|\blista|\bop[cç][oõ]es|o que (voc[eê]s |vcs )?t[eê]m|o que tem|"
    r"\btem (algum|alguma|um|uma)?\s*(suv|sed[aã]|hatch|picape|carro)|\bvoc[eê]s t[eê]m\b|\bvcs t[eê]m\b|"
    r"\bque (carros|modelos)\b|\bestoque\b|\bdispon[ií]ve",
    re.IGNORECASE,
)

_TIPO_DITO = re.compile(r"\bsuvs?\b|\bsed[aã]n?s?\b|\bhatch|\bpicapes?\b|\bpick[\s-]?ups?\b|caminhonete|camionete"
                        r"|utilit[aá]rio", re.IGNORECASE)

# Resposta ao "gostou de algum?": escolheu, recusou ou opinou sobre a lista
_OPINOU_LISTA = re.compile(
    r"gost|curt|interess|nenhum|esse|este|desse|deste|o primeiro|o segundo|o [uú]ltimo|o mais (barato|novo)"
    r"|achei|parece|caro|outr[oa]s? op",  # "sim"/"não" soltos podem ser resposta de outra coisa ("financiado sim")
    re.IGNORECASE,
)

# Depois da lista, o cliente responde se gostou de algum: o agente reage e só então segue o checklist
ESCOLHA_RESPONDIDA = (
    "Na sua mensagem anterior você mostrou carros e perguntou se o cliente gostou de algum. NÃO mostre a lista de "
    "novo. Reaja à resposta dele: se escolheu um, comente algo verdadeiro daquele carro (ano, km ou preço da lista "
    "que você mostrou); se não gostou de nenhum, pergunte o que ele procura de diferente e não siga o checklist "
    "agora. {depois}"
)

# Frases que denunciam robô. Se aparecerem, o agente reescreve uma vez (mesmo conteúdo, jeito humano).
_ROBOTICO = re.compile(
    r"\baqui est[aã]o\b|\bseguem?\s+(abaixo|as\s+op)|\btemos\s+algumas\s+op[cç][oõ]es|como\s+posso\s+ajud[aá]-?l[oa]"
    r"|fico\s+[àa]\s+disposi|estou\s+aqui\s+para\s+(te\s+)?ajudar|\bprezad|caro\s+cliente|entendi\s+sua\s+solicita"
    r"|\bcertamente\b|confira\s+(abaixo|as\s+op)|veja\s+as\s+op[cç][oõ]es"
    r"|qualificad|\blead\b|checklist|transfer[eê]ncia|transferid[oa]",  # jargão interno não vai para o cliente
    re.IGNORECASE,
)

def instrucao_estilo(original: str) -> str:
    return (
        "Reescreva a resposta abaixo com EXATAMENTE o mesmo conteúdo: as mesmas informações, os mesmos carros "
        "(se houver) e a mesma pergunta final. Não acrescente nada que não esteja nela: se ela não tem carros, a "
        "nova também não tem. Mude só o jeito: como uma vendedora de verdade no WhatsApp, reagindo ao que o "
        "cliente acabou de dizer, sem frases prontas como \"Aqui estão\", \"Temos algumas opções\", \"Como "
        "posso ajudar\", \"Estou aqui para ajudar\". Não chame ferramentas, só escreva a resposta.\n\n"
        f"RESPOSTA A REESCREVER:\n{original}"
    )


def limpar_formatacao(texto: str) -> str:
    """WhatsApp não mostra markdown: tira **negrito**, # títulos e linhas em branco entre itens da lista."""
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", texto)
    t = re.sub(r"__(.+?)__", r"\1", t)
    t = re.sub(r"^#+\s*", "", t, flags=re.MULTILINE)
    t = re.sub(r"\s*\|\s*", " · ", t) if "|" in t and "·" not in t else t
    # Linhas de carro (com km e R$): uma por linha, todas com "- ", sem linha em branco entre elas
    linhas = t.split("\n")
    carro = re.compile(r"km.*R\$|R\$.*km", re.IGNORECASE)
    com_carro = [i for i, l in enumerate(linhas) if carro.search(l)]
    if len(com_carro) >= 2:  # um carro só é uma frase ("Temos o BYD Dolphin 2024, com 5 km, por R$ 89.000"), não lista
        for i in com_carro:
            if not linhas[i].lstrip().startswith(("-", "•")):
                linhas[i] = "- " + linhas[i].strip()
    t = "\n".join(linhas)
    for _ in range(2):  # 2x: pares de itens sobrepostos
        t = re.sub(r"(\n\s*[-•][^\n]*)\n\s*\n(?=\s*[-•])", r"\1\n", t)
    t = re.sub(r"^\s*([-•][^\n]*)\n\s*\n(?=\s*[-•])", r"\1\n", t)  # lista no começo do texto
    return t.strip()

def faltando_para_qualificar(ficha: dict) -> list[str]:
    """Das 3 perguntas, as que ainda não têm resposta de verdade (o que impede a qualificação)."""
    f = ficha or {}
    falta = []
    if f.get("intencao") == "vender":
        if not (f.get("troca_modelo") and f.get("troca_ano")):
            falta.append("qual é o carro que ele quer vender (modelo e ano)")
        if not f.get("visita"):
            falta.append("que dia e período ele pode trazer o carro na loja para a avaliação")
        return falta
    if not (f.get("preco_max") or f.get("preco_min")):
        falta.append("até quanto ele pensa em investir")
    if not (f.get("tipo_interesse") or f.get("modelos_interesse")):
        falta.append("que tipo de carro ele procura (SUV, sedã, hatch ou picape)")
    if f.get("quer_financiar") is None:
        falta.append("se vai pagar à vista ou financiar")
    if not f.get("visita"):
        falta.append("que dia e período ele consegue vir na loja ver o carro")
    return falta


COBRANCA = (
    "Você disse ao cliente que vai passar a conversa para um vendedor, mas não chamou a ferramenta "
    "transferir_humano. Chame transferir_humano agora, com o motivo e a urgência corretos, e depois "
    "escreva a resposta final ao cliente."
)


def _aberturas_usadas(lead_id: int, n: int = 4) -> list[str]:
    """As primeiras palavras das últimas respostas do agente (para ele não começar sempre igual)."""
    saida = []
    for m in repo.historico(lead_id, 12):
        if m.papel == "agente" and m.texto.strip():
            primeira = re.split(r"(?<=[.!?])\s|\n", m.texto.strip(), maxsplit=1)[0]
            saida.append(" ".join(primeira.split()[:5]))
    return saida[-n:]


def _transferiu(msgs: list[BaseMessage]) -> bool:
    """Só conta a transferência que deu certo (a tool pode recusar 'qualificado' antes da hora)."""
    return any(
        isinstance(m, ToolMessage) and m.name == "transferir_humano" and '"ok": true' in str(m.content)
        for m in msgs
    )


def _so_no_contexto(modelo: str, mensagem: str, contexto: str) -> bool:
    """O modelo não foi dito nesta mensagem, mas aparece na conversa: o LLM copiou do contexto.

    Se não aparece em lugar nenhum (ex.: "biotipo" no áudio que é BYD), vale: foi uma correção da extração.
    """
    palavras = [p.lower() for p in re.findall(r"[\wÀ-ú-]+", modelo) if len(p) > 2]
    if not palavras:
        return False
    dito = any(p in mensagem.lower() for p in palavras)
    return not dito and any(p in (contexto or "").lower() for p in palavras)


def _construir(lead_id: int, callbacks: list):
    tools = criar_tools(lead_id, callbacks)
    transferir_tool = next(t for t in tools if t.name == "transferir_humano")
    modelo = llm.chat().bind_tools(tools)
    # Na mensagem que passa para o vendedor, o agente só pode transferir: buscar_estoque ali
    # virava uma lista extra depois do "o vendedor vai te chamar" (caso c21)
    so_transferir = llm.chat().bind_tools([transferir_tool])

    def extrair_dados(estado: Estado) -> dict:
        """Lê a última mensagem do cliente e grava na ficha o que ele informou."""
        hist = repo.historico(lead_id, 6)
        ultima = next((m for m in reversed(hist) if m.papel == "cliente"), None)
        if not ultima:
            return {}
        contexto = "\n".join(
            f"{'Cliente' if m.papel == 'cliente' else 'Atendente'}: {m.texto}" for m in hist if m is not ultima
        )
        ext = ficha_mod.extrair(ultima.texto, contexto, callbacks)
        # Modelo/marca que só aparece no que a Lia escreveu ("Fiat Cronos") não é interesse novo do cliente
        if ext.modelos_interesse:
            ext.modelos_interesse = [m for m in ext.modelos_interesse
                                     if not _so_no_contexto(m, ultima.texto, contexto)] or None
        # Tipo de carro só vale se o cliente disse o tipo ou um modelo (o LLM às vezes deduz "família" -> SUV)
        if ext.tipo_interesse and not ext.modelos_interesse and not _TIPO_DITO.search(ultima.texto):
            ext.tipo_interesse = None
        por_regra = ficha_mod.motivo_por_regra(ultima.texto)
        # "Pedir uma pessoa" precisa estar escrito: o LLM às vezes lê "você é um robô?" como pedido de humano
        if ext.transferir == "pediu_humano" and por_regra != "pediu_humano":
            ext.transferir = None
        if not ext.transferir:
            ext.transferir = por_regra
        antes = dict(repo.obter_lead(lead_id).ficha or {})
        # A última resposta mostrou carros e perguntou "gostou de algum?": esta mensagem é a resposta,
        # se ele escolheu ou recusou. Se respondeu outra coisa ("É 2017"), segue o checklist normal.
        escolha_respondida = antes.get("escolha") == "perguntada"
        if escolha_respondida:
            repo.atualizar_ficha(lead_id, {"escolha": "respondida"})
            escolha_respondida = bool(ext.modelos_interesse or _OPINOU_LISTA.search(ultima.texto))
        elif any(m.papel == "agente" for m in hist):
            # a última resposta do agente perguntou o próximo item do checklist: conta a pergunta
            ficha_mod.registrar_pergunta(lead_id, antes)
        depois = ficha_mod.aplicar(lead_id, ext, ultima.texto)
        if ext.transferir == "urgencia":
            repo.atualizar_ficha(lead_id, {"urgencia": "alta"})
        # modelos de interesse que sobraram depois de tirar o carro de troca
        modelos = [m for m in (ext.modelos_interesse or []) if m in (depois.get("modelos_interesse") or [])]
        ext.modelos_interesse = modelos or None
        # A lista sai assim que ele diz o tipo, o modelo ou o valor (pedido da loja: o estoque é pequeno)
        novo = ficha_mod.mudou_interesse(antes, ext)
        ficha_agora = repo.obter_lead(lead_id).ficha or {}
        tem_valor = bool(ficha_agora.get("preco_max") or ficha_agora.get("preco_min"))
        pediu_lista = bool(_PEDIU_LISTA.search(ultima.texto))
        # Pediu para ver ("quais vocês têm?") e já sabemos o que procura: mostra a lista, mesmo sem nada novo
        if pediu_lista and not novo and (ficha_agora.get("tipo_interesse") or tem_valor):
            novo = True
        # Escolheu um carro da lista que acabou de ver: não mostra a lista de novo, reage e segue
        if escolha_respondida and novo and modelos and not pediu_lista and all(repo.buscar_carros(texto=m) for m in modelos):
            novo = False
        # Visita combinada = lead qualificado: vai para o vendedor com resumo (regra em código)
        ficha_final = repo.obter_lead(lead_id).ficha or {}
        if not ext.transferir and ficha_mod.qualificado(ficha_final):
            ext.transferir = "qualificado"
            para = "avaliação do carro" if ficha_final.get("intencao") == "vender" else "ver o carro"
            repo.atualizar_ficha(lead_id, {"proximo_passo": f"Visita na loja ({para}): {ficha_final['visita']}. "
                                                            "Vendedor confirmar o horário."})
        return {"transferir": ext.transferir, "mostrar_carros": novo, "modelos_agora": modelos,
                "escolha_respondida": escolha_respondida and not novo}

    def carregar_contexto(estado: Estado) -> dict:
        lead = repo.obter_lead(lead_id)
        ficha = lead.ficha or {}
        msgs: list[BaseMessage] = [SystemMessage(prompt_sistema(ficha, lead.nome, ficha_mod.checklist(ficha)))]
        for m in repo.historico(lead_id):
            if m.papel == "cliente":
                msgs.append(HumanMessage(m.texto))
            else:
                msgs.append(AIMessage(m.texto))
        # Não repetir o jeito de começar: mostra ao agente as aberturas que ele já usou nesta conversa
        aberturas = _aberturas_usadas(lead_id)
        comentados = comentarios_feitos(lead_id)
        if aberturas or comentados:
            estilo = "[estilo] "
            if aberturas:
                estilo += ("Você já começou mensagens assim nesta conversa: " + " | ".join(f'"{a}"' for a in aberturas)
                           + ". Comece esta de um jeito diferente (ou vá direto ao ponto). ")
            if comentados:
                estilo += ("Comentários de vendedor que você JÁ fez: " + " | ".join(f'"{c}"' for c in comentados)
                           + ". Não repita nenhum deles nem a mesma ideia.")
            msgs.append(SystemMessage(estilo))
        if not any(m.papel == "agente" for m in repo.historico(lead_id, 20)):
            msgs.append(SystemMessage(
                f"[instrução para esta resposta] É a sua primeira mensagem nesta conversa: comece cumprimentando e se "
                f"apresentando em uma frase curta (ex.: \"Boa noite! Aqui é a {settings.nome_atendente}, da "
                f"{settings.nome_loja} 😊\") e depois atenda o que o cliente pediu."))
        ultima_cliente = next((m.texto for m in reversed(repo.historico(lead_id, 4)) if m.papel == "cliente"), "")
        if _PERGUNTA_ROBO.search(ultima_cliente):
            msgs.append(SystemMessage("[instrução para esta resposta] "
                                      + HONESTIDADE.format(atendente=settings.nome_atendente)))
        # Ordem do turno numa mensagem própria, logo antes da resposta (mais peso que no fim do prompt)
        motivo = estado.get("transferir")
        if motivo == "qualificado" and estado.get("mostrar_carros"):
            # Qualificou na mesma mensagem em que disse o que procura: mostra os carros E passa para o vendedor
            msgs.append(SystemMessage(
                f"[instrução para esta resposta] {sugestoes(ficha, estado.get('modelos_agora'), comentarios_feitos(lead_id), textos_do_agente(lead_id))}\n"
                f"{ficha_mod.acao_obrigatoria(motivo, ficha, com_lista=True)} Mostre os carros acima ANTES de avisar do vendedor."))
        elif motivo:
            msgs.append(SystemMessage(f"[instrução para esta resposta] {ficha_mod.acao_obrigatoria(motivo, ficha)}"))
        elif estado.get("mostrar_carros"):
            texto = sugestoes(ficha, estado.get("modelos_agora"), comentarios_feitos(lead_id), textos_do_agente(lead_id))
            if PERGUNTA_ESCOLHA in texto:  # a lista termina em "gostou de algum?": a próxima mensagem é a resposta
                repo.atualizar_ficha(lead_id, {"escolha": "perguntada"})
            msgs.append(SystemMessage(f"[instrução para esta resposta] {texto}"))
        elif estado.get("escolha_respondida"):
            prox = ficha_mod.proxima_pergunta(ficha)
            depois = f"Se ele escolheu um, no fim pergunte SÓ isto: {prox}." if prox else ""
            msgs.append(SystemMessage(f"[instrução para esta resposta] {ESCOLHA_RESPONDIDA.format(depois=depois)}"))
        elif (prox := ficha_mod.proxima_pergunta(ficha)) and ficha_mod.pendentes(ficha)[0] != "visita":
            # Conversa comum: a pergunta de agora é a do checklist, uma só (sem isso ela perguntava o tipo antes do valor)
            rotulos = {campo: rot for campo, rot, *_ in ficha_mod._itens(ficha)}
            outros = [rotulos[c] for c in ficha_mod.pendentes(ficha)[1:]]
            nao_agora = f" Ainda NÃO pergunte sobre: {', '.join(outros)}." if outros else ""
            msgs.append(SystemMessage(f"[instrução para esta resposta] No fim da mensagem, pergunte SÓ isto: {prox}. "
                                      f"Uma única pergunta, sem juntar com outra.{nao_agora}"))
        return {"messages": msgs}

    def agente(estado: Estado) -> dict:
        # se a mensagem que transfere também mostra carros, a lista já vem pronta do código (sugestoes)
        return {"messages": [(so_transferir if estado.get("transferir") else modelo).invoke(estado["messages"])]}

    def checar_transferencia(estado: Estado) -> dict:
        """Garantia em código.

        - Prometeu transferir (ou a extração detectou motivo) e não chamou a tool: cobra o agente 1 vez.
        - Motivo detectado e mesmo assim não transferiu: o código transfere e responde com a mensagem padrão.
        """
        final = estado["messages"][-1]
        texto = final.content if isinstance(final, AIMessage) else ""
        motivo = estado.get("transferir")
        # Perguntou se é robô e a resposta desviou? Reescreve 1 vez, sendo honesta.
        ultima_cliente = next((m.texto for m in reversed(repo.historico(lead_id, 4)) if m.papel == "cliente"), "")
        if (texto and not estado.get("cobrado_honestidade") and _PERGUNTA_ROBO.search(ultima_cliente)
                and not _DIZ_VIRTUAL.search(texto)):
            return {"messages": [HumanMessage(
                "[instrução interna] " + HONESTIDADE.format(atendente=settings.nome_atendente)
                + " Reescreva sua resposta assim, sem chamar ferramentas.")], "cobrado_honestidade": True}
        # Soou como robô? Reescreve 1 vez, com o mesmo conteúdo.
        if texto and not estado.get("cobrado_estilo") and _ROBOTICO.search(texto):
            return {"messages": [HumanMessage(f"[instrução interna] {instrucao_estilo(texto)}")],
                    "cobrado_estilo": True}
        # Ofereceu visita/test drive antes da hora? Cobra 1 vez para seguir a qualificação.
        # A visita é o último passo: vale quando ela é a próxima pergunta ou quando o próprio cliente falou de visita.
        if (not motivo and not estado.get("cobrado_cedo") and _OFERECE_VISITA.search(texto or "")
                and not _OFERECE_VISITA.search(ultima_cliente) and not re.search(r"\b(ir|passar|vou) a[ií]\b|loja",
                                                                                  ultima_cliente, re.I)):
            falta = ficha_mod.pendentes(repo.obter_lead(lead_id).ficha or {})
            if falta and falta[0] != "visita" and repo.obter_lead(lead_id).status == "bot":
                return {"messages": [HumanMessage(
                    f"[instrução interna] Ainda faltam itens do checklist ({', '.join(falta)}). Não ofereça "
                    f"vendedor, visita nem test drive ainda. Reescreva a resposta ao cliente fazendo a próxima "
                    f"pergunta do checklist ({falta[0]}).")], "cobrado_cedo": True}
        precisa = bool(motivo) or bool(_PROMETEU_TRANSFERIR.search(texto or ""))
        if not precisa or _transferiu(estado["messages"]) or repo.obter_lead(lead_id).status == "humano":
            return {}
        if not estado.get("cobrado"):
            return {"messages": [HumanMessage(f"[instrução interna] {COBRANCA}")], "cobrado": True}
        # Prometeu vendedor, mas a transferência não é permitida (ex.: tool recusou "qualificado" porque falta
        # resposta): a promessa não pode chegar ao cliente. Reescreve 1 vez perguntando o que falta.
        if not motivo and not estado.get("cobrado_promessa") and _PROMETEU_TRANSFERIR.search(texto or ""):
            falta = faltando_para_qualificar(repo.obter_lead(lead_id).ficha or {})
            return {"messages": [HumanMessage(
                "[instrução interna] Você NÃO pode passar para um vendedor agora: ainda falta saber "
                f"{' e '.join(falta) or 'o que o cliente procura'}. Reescreva a resposta ao cliente SEM prometer "
                "vendedor, visita ou test drive e SEM dizer que já tem tudo: reaja ao que ele disse e pergunte, de "
                f"um jeito simples, {falta[0] if falta else 'o que ele procura'}. Não chame ferramentas.")],
                "cobrado_promessa": True}
        if motivo:
            urgencia = "alta" if motivo in ("urgencia", "reclamacao") else "baixa" if motivo == "qualificado" else "media"
            resultado = transferir_tool.invoke({"motivo": motivo, "urgencia": urgencia})
            return {"messages": [
                ToolMessage(resultado, name="transferir_humano", tool_call_id="transferencia-pelo-codigo"),
                AIMessage(fixa("transferencia")),
            ]}
        return {}

    def depois_da_checagem(estado: Estado) -> str:
        return "agente" if isinstance(estado["messages"][-1], HumanMessage) else "resposta"

    def resposta(estado: Estado) -> dict:
        return {}

    g = StateGraph(Estado)
    g.add_node("extrair_dados", extrair_dados)
    g.add_node("carregar_contexto", carregar_contexto)
    g.add_node("agente", agente)
    g.add_node("tools", ToolNode(tools))
    g.add_node("checar_transferencia", checar_transferencia)
    g.add_node("resposta", resposta)
    g.add_edge(START, "extrair_dados")
    g.add_edge("extrair_dados", "carregar_contexto")
    g.add_edge("carregar_contexto", "agente")
    g.add_conditional_edges("agente", tools_condition, {"tools": "tools", END: "checar_transferencia"})
    g.add_edge("tools", "agente")
    g.add_conditional_edges("checar_transferencia", depois_da_checagem, {"agente": "agente", "resposta": "resposta"})
    g.add_edge("resposta", END)
    return g.compile()


def rodar_agente(lead_id: int, callbacks: list | None = None) -> tuple[str, list[str]]:
    """Roda o agente sobre o histórico do lead. Retorna (texto da resposta, tools chamadas)."""
    callbacks = callbacks or []
    grafo = _construir(lead_id, callbacks)
    final = grafo.invoke(
        {"messages": [], "lead_id": lead_id, "cobrado": False, "transferir": None, "mostrar_carros": False,
         "modelos_agora": [], "cobrado_cedo": False, "cobrado_estilo": False, "cobrado_honestidade": False,
         "escolha_respondida": False, "cobrado_promessa": False},
        config={"callbacks": callbacks, "recursion_limit": 14, "run_name": "agente"},
    )
    msgs = final["messages"]
    tools_chamadas = [
        m.name if '"ok": false' not in str(m.content) else f"{m.name} (recusada)"
        for m in msgs if isinstance(m, ToolMessage)
    ]
    ultima = next((m for m in reversed(msgs) if isinstance(m, AIMessage) and m.content), None)
    texto = limpar_formatacao(ultima.content) if ultima else ""
    return texto, tools_chamadas
