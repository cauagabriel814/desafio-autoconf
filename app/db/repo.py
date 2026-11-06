"""Acesso ao banco usado pelo pipeline, pelas tools e pelas telas."""

import unicodedata
from datetime import datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db.modelos import Carro, Lead, Mensagem, Transferencia
from app.db.sessao import SessionLocal


def _sem_acento(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn").lower()


# ---------- Leads ----------

def obter_ou_criar_lead(canal: str, cliente_id: str, nome: str | None = None) -> Lead:
    with SessionLocal() as s:
        lead = s.scalar(select(Lead).where(Lead.canal == canal, Lead.cliente_id == cliente_id))
        if not lead:
            lead = Lead(canal=canal, cliente_id=cliente_id, nome=nome, ficha={})
            s.add(lead)
            s.commit()
        elif nome and not lead.nome:
            lead.nome = nome
            s.commit()
        return lead


def obter_lead(lead_id: int) -> Lead | None:
    with SessionLocal() as s:
        return s.get(Lead, lead_id)


def abrir_sessao(lead_id: int) -> None:
    with SessionLocal() as s:
        lead = s.get(Lead, lead_id)
        lead.sessao_ate = datetime.now() + timedelta(minutes=settings.sessao_minutos)
        s.commit()


def sessao_ativa(lead: Lead) -> bool:
    return bool(lead.sessao_ate and lead.sessao_ate > datetime.now())


def atualizar_ficha(lead_id: int, campos: dict) -> dict:
    with SessionLocal() as s:
        lead = s.get(Lead, lead_id)
        ficha = dict(lead.ficha or {})
        for k, v in campos.items():
            if v is None:
                continue
            if k == "modelos_interesse":
                atuais = ficha.get(k) or []
                v = atuais + [m for m in v if m not in atuais]
            ficha[k] = v
        lead.ficha = ficha
        if campos.get("nome") and not lead.nome:
            lead.nome = campos["nome"]
        s.commit()
        return ficha


def arquivar_lead(lead_id: int) -> None:
    """Tira o lead do número atual (fica no painel como arquivado). A próxima mensagem cria um lead novo."""
    with SessionLocal() as s:
        lead = s.get(Lead, lead_id)
        lead.cliente_id = f"{lead.cliente_id}~{datetime.now():%Y%m%d%H%M%S%f}"
        lead.status = "arquivado"
        lead.sessao_ate = None
        s.commit()


def definir_status(lead_id: int, status: str) -> None:
    with SessionLocal() as s:
        lead = s.get(Lead, lead_id)
        lead.status = status
        s.commit()


def _filtros_leads(status: str | None, urgencia: str | None, canais: tuple[str, ...] | None) -> list:
    cond = []
    if status:
        cond.append(Lead.status == status)
    if urgencia:
        cond.append(Lead.ficha["urgencia"].as_string() == urgencia)
    if canais:
        cond.append(Lead.canal.in_(canais))
    return cond


def _ordem_leads() -> list:
    """Transferidos primeiro, depois urgência (alta > media > baixa) e os mais recentes."""
    urg = Lead.ficha["urgencia"].as_string()  # JSON portável (SQLite e Postgres)
    return [
        case((Lead.status == "humano", 0), else_=1),
        case((urg == "alta", 0), (urg == "media", 1), (urg == "baixa", 2), else_=3),
        Lead.atualizado_em.desc(),
    ]


def _lead_resumo(l: Lead) -> dict:
    ficha = l.ficha or {}
    ult = l.transferencias[-1] if l.transferencias else None
    return {
        "id": l.id,
        "cliente_id": l.cliente_id,
        "nome": l.nome,
        "canal": l.canal,
        "status": l.status,
        "urgencia": ficha.get("urgencia"),
        "motivo": ult.motivo if ult else None,
        "atualizado_em": l.atualizado_em,
        "ficha": ficha,
    }


def listar_leads(status: str | None = None, urgencia: str | None = None,
                 canais: tuple[str, ...] | None = None) -> list[dict]:
    """Todos os leads (filtro, ordem e transferências resolvidos no banco, sem uma consulta por lead)."""
    with SessionLocal() as s:
        q = (select(Lead).where(*_filtros_leads(status, urgencia, canais)).order_by(*_ordem_leads())
             .options(selectinload(Lead.transferencias)))
        return [_lead_resumo(l) for l in s.scalars(q).all()]


def pagina_leads(status: str | None, urgencia: str | None, canais: tuple[str, ...] | None,
                 pagina: int, por_pagina: int) -> tuple[list[dict], int]:
    """Uma página de leads + o total. Só a página sai do banco."""
    cond = _filtros_leads(status, urgencia, canais)
    with SessionLocal() as s:
        total = s.scalar(select(func.count(Lead.id)).where(*cond)) or 0
        paginas = max(1, -(-total // por_pagina))
        pagina = min(max(1, pagina), paginas)
        q = (select(Lead).where(*cond).order_by(*_ordem_leads())
             .offset((pagina - 1) * por_pagina).limit(por_pagina)
             .options(selectinload(Lead.transferencias)))
        return [_lead_resumo(l) for l in s.scalars(q).all()], total


def leads_para_exportar(status: str | None = None, ids: list[int] | None = None) -> list[dict]:
    """Leads com ficha e transferências (sem as mensagens), numa consulta só."""
    with SessionLocal() as s:
        q = select(Lead).options(selectinload(Lead.transferencias)).order_by(*_ordem_leads())
        if status:
            q = q.where(Lead.status == status)
        if ids:
            q = q.where(Lead.id.in_(ids))
        saida = []
        for l in s.scalars(q).all():
            d = _lead_resumo(l)
            d["transferencias"] = [
                {"motivo": t.motivo, "urgencia": t.urgencia, "resumo": t.resumo, "criado_em": t.criado_em}
                for t in l.transferencias
            ]
            saida.append(d)
        return saida


def detalhe_lead(lead_id: int) -> dict | None:
    with SessionLocal() as s:
        l = s.get(Lead, lead_id)
        if not l:
            return None
        return {
            "id": l.id,
            "cliente_id": l.cliente_id,
            "nome": l.nome,
            "canal": l.canal,
            "status": l.status,
            "ficha": l.ficha or {},
            "transferencias": [
                {"motivo": t.motivo, "urgencia": t.urgencia, "resumo": t.resumo, "criado_em": t.criado_em}
                for t in l.transferencias
            ],
            "mensagens": [
                {
                    "papel": m.papel,
                    "texto": m.texto,
                    "tipo_origem": m.tipo_origem,
                    "status": m.status,
                    "motivo_bloqueio": m.motivo_bloqueio,
                    "audio_path": m.audio_path,
                    "criado_em": m.criado_em,
                }
                for m in l.mensagens
            ],
        }


# ---------- Mensagens ----------

def salvar_mensagem(
    lead_id: int,
    papel: str,
    texto: str,
    tipo_origem: str = "texto",
    status: str = "recebida",
    motivo_bloqueio: str | None = None,
    audio_path: str | None = None,
    message_id: str | None = None,
) -> int:
    with SessionLocal() as s:
        m = Mensagem(
            lead_id=lead_id,
            papel=papel,
            texto=texto,
            tipo_origem=tipo_origem,
            status=status,
            motivo_bloqueio=motivo_bloqueio,
            audio_path=audio_path,
            message_id=message_id,
        )
        s.add(m)
        lead = s.get(Lead, lead_id)
        lead.atualizado_em = datetime.now()
        s.commit()
        return m.id


def mensagem_ja_existe(message_id: str) -> bool:
    with SessionLocal() as s:
        return bool(s.scalar(select(Mensagem.id).where(Mensagem.message_id == message_id).limit(1)))


def historico(lead_id: int, n: int | None = None) -> list[Mensagem]:
    """Últimas N mensagens válidas da conversa (sem as respostas bloqueadas)."""
    n = n or settings.historico_n
    with SessionLocal() as s:
        q = (
            select(Mensagem)
            .where(Mensagem.lead_id == lead_id, Mensagem.status.in_(["recebida", "enviada", "ignorada"]))
            .order_by(Mensagem.id.desc())
            .limit(n)
        )
        return list(reversed(s.scalars(q).all()))


# ---------- Carros ----------

_vocab_estoque: tuple[float, set[str], set[str]] = (0.0, set(), set())


def _vocabulario_estoque() -> tuple[set[str], set[str]]:
    """(marcas, palavras de marca e modelo) do estoque, sem acento. Guardado por 5 s (muda quando se edita o estoque)."""
    global _vocab_estoque
    agora = datetime.now().timestamp()
    if agora - _vocab_estoque[0] > 5:
        with SessionLocal() as s:
            carros = s.scalars(select(Carro)).all()
        marcas = {_sem_acento(c.marca) for c in carros}
        palavras = {p for c in carros for p in _sem_acento(f"{c.marca} {c.modelo}").split()}
        _vocab_estoque = (agora, marcas, palavras)
    return _vocab_estoque[1], _vocab_estoque[2]


def invalidar_vocabulario() -> None:
    """Chamado quando o estoque muda (adicionar, editar, excluir, importar)."""
    global _vocab_estoque
    _vocab_estoque = (0.0, set(), set())


def combina_texto(texto: str, carro: dict) -> bool:
    """O carro bate com o que o cliente disse?

    - "Jeep Renegade" não é o Compass: o modelo citado (a 1ª palavra que não é marca) precisa existir no estoque.
    - Números ("1.0", "2022") e palavras que nenhum carro do estoque tem ("Turbo", "Flex") não atrapalham:
      "Chevrolet Onix LT 1.0 Turbo 2022" bate com o Onix LT.
    - As palavras que o estoque conhece (Corolla, Cross, LT...) precisam estar todas neste carro.
    """
    marcas, conhecidas = _vocabulario_estoque()
    alvo = set(_sem_acento(f"{carro['marca']} {carro['modelo']}").split())
    termos = [t for t in _sem_acento(texto).split() if len(t) > 1 and not t.replace(".", "").replace(",", "").isdigit()]
    if not termos:
        return False
    modelo_citado = next((t for t in termos if t not in marcas), None)
    if modelo_citado is not None and modelo_citado not in conhecidas:
        return False  # modelo que a loja não tem (Renegade, Hilux, Ferrari...)
    return all(t in alvo for t in termos if t in conhecidas)


def buscar_carros(
    tipo: str | None = None,
    preco_max: int | None = None,
    preco_min: int | None = None,
    ano_min: int | None = None,
    texto: str | None = None,
    apenas_disponiveis: bool = True,
) -> list[dict]:
    with SessionLocal() as s:
        q = select(Carro)
        if apenas_disponiveis:
            q = q.where(Carro.disponivel.is_(True))
        if preco_max:
            q = q.where(Carro.preco <= preco_max)
        if preco_min:
            q = q.where(Carro.preco >= preco_min)
        if ano_min:
            q = q.where(Carro.ano >= ano_min)
        carros = s.scalars(q.order_by(Carro.preco)).all()
    saida = []
    for c in carros:
        if tipo and _sem_acento(c.tipo) != _sem_acento(tipo):
            continue
        if texto:
            alvo = _sem_acento(f"{c.marca} {c.modelo}")
            termos = [t for t in _sem_acento(texto).split() if len(t) > 1]
            if not any(t in alvo for t in termos):
                continue
        saida.append(c.como_dict())
    return saida


def obter_carro(carro_id: int) -> dict | None:
    with SessionLocal() as s:
        c = s.get(Carro, carro_id)
        return c.como_dict() if c else None


def listar_carros() -> list[dict]:
    with SessionLocal() as s:
        return [c.como_dict() for c in s.scalars(select(Carro).order_by(Carro.marca, Carro.modelo)).all()]


def salvar_carro(dados: dict, carro_id: int | None = None) -> dict:
    with SessionLocal() as s:
        c = s.get(Carro, carro_id) if carro_id else Carro()
        if carro_id and not c:
            raise KeyError(carro_id)
        for k in ("marca", "modelo", "ano", "km", "preco", "tipo", "disponivel"):
            if k in dados:
                setattr(c, k, dados[k])
        if not carro_id:
            s.add(c)
        s.commit()
        invalidar_vocabulario()
        return c.como_dict()


def excluir_carro(carro_id: int) -> bool:
    with SessionLocal() as s:
        c = s.get(Carro, carro_id)
        if not c:
            return False
        s.delete(c)
        s.commit()
        invalidar_vocabulario()
        return True


# ---------- Transferências ----------

def registrar_transferencia(lead_id: int, motivo: str, urgencia: str, resumo: str) -> None:
    with SessionLocal() as s:
        s.add(Transferencia(lead_id=lead_id, motivo=motivo, urgencia=urgencia, resumo=resumo))
        lead = s.get(Lead, lead_id)
        lead.status = "humano"
        s.commit()
