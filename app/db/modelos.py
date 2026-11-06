from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.sessao import Base


def agora() -> datetime:
    return datetime.now()


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (UniqueConstraint("canal", "cliente_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canal: Mapped[str] = mapped_column(String(20))  # whatsapp | web | terminal
    cliente_id: Mapped[str] = mapped_column(String(80))
    nome: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="bot")  # bot | humano
    ficha: Mapped[dict] = mapped_column(JSON, default=dict)
    sessao_ate: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)
    atualizado_em: Mapped[datetime] = mapped_column(DateTime, default=agora, onupdate=agora)

    mensagens: Mapped[list["Mensagem"]] = relationship(back_populates="lead", order_by="Mensagem.id")
    transferencias: Mapped[list["Transferencia"]] = relationship(back_populates="lead", order_by="Transferencia.id")


class Mensagem(Base):
    __tablename__ = "mensagens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"))
    papel: Mapped[str] = mapped_column(String(10))  # cliente | agente
    texto: Mapped[str] = mapped_column(Text)
    tipo_origem: Mapped[str] = mapped_column(String(10), default="texto")  # texto | audio | imagem
    # recebida | enviada | bloqueada | ignorada (cliente com humano)
    status: Mapped[str] = mapped_column(String(10), default="recebida")
    motivo_bloqueio: Mapped[str | None] = mapped_column(Text, nullable=True)
    audio_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)

    lead: Mapped[Lead] = relationship(back_populates="mensagens")


class Carro(Base):
    __tablename__ = "carros"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    marca: Mapped[str] = mapped_column(String(40))
    modelo: Mapped[str] = mapped_column(String(80))
    ano: Mapped[int] = mapped_column(Integer)
    km: Mapped[int] = mapped_column(Integer)
    preco: Mapped[int] = mapped_column(Integer)
    tipo: Mapped[str] = mapped_column(String(20))
    disponivel: Mapped[bool] = mapped_column(Boolean, default=True)

    @property
    def nome(self) -> str:
        return f"{self.marca} {self.modelo}"

    def como_dict(self) -> dict:
        return {
            "id": self.id,
            "marca": self.marca,
            "modelo": self.modelo,
            "nome": self.nome,
            "ano": self.ano,
            "km": self.km,
            "preco": self.preco,
            "tipo": self.tipo,
            "disponivel": self.disponivel,
        }


class Configuracao(Base):
    """Ajustes do agente feitos pela tela /painel/config (valem por cima do .env)."""

    __tablename__ = "configuracao"

    chave: Mapped[str] = mapped_column(String(60), primary_key=True)
    valor: Mapped[dict] = mapped_column(JSON)  # {"v": valor}
    atualizado_em: Mapped[datetime] = mapped_column(DateTime, default=agora, onupdate=agora)


class Transferencia(Base):
    __tablename__ = "transferencias"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"))
    motivo: Mapped[str] = mapped_column(String(30))
    urgencia: Mapped[str] = mapped_column(String(10))
    resumo: Mapped[str] = mapped_column(Text)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)

    lead: Mapped[Lead] = relationship(back_populates="transferencias")
