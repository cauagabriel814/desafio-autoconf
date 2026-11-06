import csv
import io
from pathlib import Path

from sqlalchemy import select

from app.db.modelos import Carro
from app.db.sessao import Base, SessionLocal, engine

ESTOQUE_CSV = Path(__file__).parent / "estoque.csv"


def importar_csv(conteudo: str) -> int:
    """Importa carros de um CSV (marca,modelo,ano,km,preco,tipo). Retorna quantos entraram."""
    leitor = csv.DictReader(io.StringIO(conteudo))
    n = 0
    with SessionLocal() as s:
        for linha in leitor:
            s.add(
                Carro(
                    marca=linha["marca"].strip(),
                    modelo=linha["modelo"].strip(),
                    ano=int(linha["ano"]),
                    km=int(str(linha["km"]).replace(".", "")),
                    preco=int(str(linha["preco"]).replace(".", "")),
                    tipo=linha["tipo"].strip(),
                    disponivel=str(linha.get("disponivel", "sim")).strip().lower() not in ("nao", "não", "0", "false"),
                )
            )
            n += 1
        s.commit()
    return n


def iniciar_banco() -> None:
    Base.metadata.create_all(engine)
    with SessionLocal() as s:
        tem_carro = s.scalar(select(Carro.id).limit(1))
    if not tem_carro:
        importar_csv(ESTOQUE_CSV.read_text(encoding="utf-8"))
