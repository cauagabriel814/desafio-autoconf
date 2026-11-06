"""Caminhos tristes: o que acontece quando o cliente xinga, não responde ou não tem como ser atendido."""

import uuid

from app.agente import tools
from app.canais.payload import MensagemEntrada
from app.db import repo
from app.pipeline import guard_entrada, orquestrador


def _msg(cliente: str, texto: str) -> MensagemEntrada:
    return MensagemEntrada(canal="web", cliente_id=cliente, message_id=uuid.uuid4().hex, tipo="texto",
                           conteudo=texto)


def test_segunda_ofensa_passa_para_o_vendedor(monkeypatch):
    monkeypatch.setattr(guard_entrada, "classificar",
                        lambda *a, **k: guard_entrada.Classificacao(categoria="ofensivo", motivo="teste"))
    monkeypatch.setattr(tools, "gerar_resumo", lambda *a, **k: "Motivo da transferência: teste")
    cid = f"web-{uuid.uuid4().hex[:6]}"
    lead_id = repo.obter_ou_criar_lead("web", cid).id

    r1 = orquestrador.processar([_msg(cid, "seu idiota")])
    assert r1.bloqueio_entrada == "ofensivo" and not r1.transferido  # 1ª vez: só pede calma
    assert repo.obter_lead(lead_id).status == "bot"

    r2 = orquestrador.processar([_msg(cid, "imbecil")])
    assert r2.transferido and r2.bloqueio_entrada == "ofensivo"  # 2ª vez: vendedor
    lead = repo.obter_lead(lead_id)
    assert lead.status == "humano" and lead.ficha["ofensas"] == 2
    assert repo.detalhe_lead(lead_id)["transferencias"][-1]["motivo"] == "agressivo"
