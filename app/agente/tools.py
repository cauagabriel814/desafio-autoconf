"""Tools do agente. São criadas por conversa, já presas ao lead atual."""

import json
from typing import Literal

from langchain_core.tools import tool

from app.agente import prompts
from app.db import repo
from app import llm

Urgencia = Literal["baixa", "media", "alta"]
Motivo = Literal["reclamacao", "negociacao", "pedido_credito", "urgencia", "pediu_humano", "sem_resposta",
                 "qualificado"]


def _texto_conversa(lead_id: int) -> str:
    linhas = []
    for m in repo.historico(lead_id, 30):
        quem = "Cliente" if m.papel == "cliente" else "Atendente"
        linhas.append(f"{quem}: {m.texto}")
    return "\n".join(linhas)


def gerar_resumo(lead_id: int, motivo: str, callbacks: list | None = None) -> str:
    from app.agente.ficha import ficha_para_resumo

    lead = repo.obter_lead(lead_id)
    texto = prompts.RESUMO_VENDEDOR.format(
        motivo=motivo,
        ficha=ficha_para_resumo(lead.ficha or {}),  # com "não perguntado" explícito
        conversa=_texto_conversa(lead_id),
    )
    resp = llm.chat(temperature=0).invoke(texto, config={"callbacks": callbacks or []})
    return resp.content.strip()


def criar_tools(lead_id: int, callbacks: list | None = None) -> list:
    @tool
    def buscar_estoque(
        tipo: Literal["SUV", "Sedã", "Hatch", "Picape"] | None = None,
        preco_max: int | None = None,
        preco_min: int | None = None,
        ano_min: int | None = None,
        marca_ou_modelo: str | None = None,
    ) -> str:
        """Busca carros disponíveis no estoque da loja. Use antes de citar qualquer carro ou preço.
        Todos os filtros são opcionais. Preços em reais inteiros (120 mil = 120000).
        Sem filtros, devolve o estoque inteiro."""
        carros = repo.buscar_carros(
            tipo=tipo, preco_max=preco_max, preco_min=preco_min, ano_min=ano_min, texto=marca_ou_modelo
        )
        carros.sort(key=lambda c: c["preco"])  # sempre em ordem de preço, do menor para o maior
        if not carros:
            todos = repo.buscar_carros()
            return json.dumps(
                {"encontrados": [], "aviso": "Nenhum carro com esses filtros.", "estoque_completo": todos},
                ensure_ascii=False,
            )
        return json.dumps({"encontrados": carros}, ensure_ascii=False)

    @tool
    def detalhes_carro(carro_id: int) -> str:
        """Devolve os dados completos de um carro do estoque pelo id."""
        carro = repo.obter_carro(carro_id)
        return json.dumps(carro or {"erro": "Carro não encontrado"}, ensure_ascii=False)

    @tool
    def transferir_humano(motivo: Motivo, urgencia: Urgencia = "media") -> str:
        """Passa a conversa para um vendedor. Use em reclamação, negociação (desconto, contraproposta ou
        quanto a loja paga no carro do cliente), pedido de crédito ou parcela, urgência alta, quando o
        cliente pedir uma pessoa, quando ele perguntar algo que você não sabe responder com certeza
        (motivo "sem_resposta"), ou quando a visita à loja estiver combinada (motivo "qualificado").
        Gera o resumo para o vendedor."""
        lead = repo.obter_lead(lead_id)
        if lead.status == "humano":
            return json.dumps({"ok": True, "aviso": "Conversa já estava com um vendedor."}, ensure_ascii=False)
        if motivo == "pediu_humano":
            # o agente às vezes lê "você é um robô?" como pedido de pessoa: só vale se o cliente pediu mesmo
            from app.agente.ficha import motivo_por_regra

            ultima = next((m.texto for m in reversed(repo.historico(lead_id, 6)) if m.papel == "cliente"), "")
            if motivo_por_regra(ultima) != "pediu_humano":
                return json.dumps(
                    {"ok": False, "erro": "O cliente não pediu para falar com uma pessoa. Não transfira: responda "
                                          "o que ele perguntou e siga o atendimento."},
                    ensure_ascii=False,
                )
        if motivo == "qualificado":
            from app.agente.ficha import pendentes, qualificado

            # Mesma regra do código: respostas de verdade até a visita marcada ("não sei" não qualifica)
            if not qualificado(lead.ficha or {}):
                falta = pendentes(lead.ficha or {}) or ["respostas de verdade até o dia da visita"]
                return json.dumps(
                    {"ok": False, "erro": f"Ainda não dá para passar como qualificado. Falta: {', '.join(falta)}. "
                                          "Continue o atendimento normalmente, sem oferecer vendedor."},
                    ensure_ascii=False,
                )
        # A urgência da ficha veio do cliente; o parâmetro da tool só vale se ainda não há uma
        # (ou se o motivo é a própria urgência).
        atual = (lead.ficha or {}).get("urgencia")
        if motivo == "urgencia":
            urgencia = "alta"
        elif atual:
            urgencia = atual
        repo.atualizar_ficha(lead_id, {"urgencia": urgencia})
        resumo = gerar_resumo(lead_id, motivo, callbacks)
        repo.registrar_transferencia(lead_id, motivo, urgencia, resumo)
        return json.dumps(
            {"ok": True, "mensagem": "Transferido. Avise o cliente que um vendedor vai continuar por aqui."},
            ensure_ascii=False,
        )

    return [buscar_estoque, detalhes_carro, transferir_humano]
