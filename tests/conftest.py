import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/teste.db"
os.environ["DATA_DIR"] = _tmp
os.environ["TRIGGER_KEYWORD"] = "#thread"
os.environ["OPENAI_API_KEY"] = ""
os.environ["TEST"] = "on"  # comandos #reset/#parar ligados nos testes (há testes do modo off também)
# Isola dos serviços do compose: nada de Evolution ou Langfuse de verdade nos testes
os.environ["EVOLUTION_API_URL"] = "http://127.0.0.1:9"
os.environ["LANGFUSE_PUBLIC_KEY"] = ""
os.environ["LANGFUSE_SECRET_KEY"] = ""

import pytest  # noqa: E402

from app.db.seed import iniciar_banco  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def banco():
    iniciar_banco()
