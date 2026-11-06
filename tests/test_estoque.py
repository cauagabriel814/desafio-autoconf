from app.db import repo


def nomes(carros):
    return {c["modelo"] for c in carros}


def test_estoque_completo():
    assert len(repo.buscar_carros()) == 22


def test_suv_ate_120_mil():
    assert nomes(repo.buscar_carros(tipo="SUV", preco_max=120000)) == {
        "Creta Limited", "T-Cross Comfortline", "Kicks SV", "Duster Iconic"
    }


def test_tipo_sem_acento():
    assert nomes(repo.buscar_carros(tipo="seda")) == {
        "Corolla XEi", "Civic Touring", "Sentra Advance", "Onix Plus Premier", "Cronos Drive"
    }


def test_busca_por_modelo_inexistente():
    assert repo.buscar_carros(texto="Hilux") == []


def test_busca_por_marca():
    assert nomes(repo.buscar_carros(texto="toyota")) == {"Corolla XEi", "Corolla Cross XRE"}


def test_picapes():
    assert nomes(repo.buscar_carros(tipo="Picape")) == {"Toro Volcano Diesel", "S10 LTZ Diesel", "Ranger XLS"}


def test_vendido_nao_aparece():
    kwid = repo.buscar_carros(texto="kwid")[0]
    repo.salvar_carro({"disponivel": False}, kwid["id"])
    try:
        assert repo.buscar_carros(texto="kwid") == []
    finally:
        repo.salvar_carro({"disponivel": True}, kwid["id"])
