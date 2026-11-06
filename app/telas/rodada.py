"""Roda o simulador a partir da tela Testes, em segundo plano (um processo por vez)."""

import re
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter()

LOG = Path("relatorios") / "rodada.log"
AO_VIVO = Path("relatorios") / "ao_vivo.json"  # o simulador regrava a cada caso que termina

# grupo -> (rótulo, argumentos do simulador)
GRUPOS = {
    "pdf": ("Casos do PDF", ["1", "2", "3", "4", "5", "6"]),
    "coleta": ("Coleta básica", ["c"]),
    "autoconf": ("Respostas da Autoconf", ["a"]),
    "todos": ("Todos (com humanidade)", ["--tom"]),
}
CASOS_YAML = Path(__file__).resolve().parents[2] / "simulador" / "casos.yaml"


def ids_do_grupo(grupo: str) -> list[str]:
    """Os casos que o simulador vai rodar, na mesma ordem (mesmo filtro por prefixo)."""
    import yaml

    try:
        ids = [c["id"] for c in yaml.safe_load(CASOS_YAML.read_text(encoding="utf-8"))]
    except (OSError, ValueError, TypeError, KeyError):
        return []
    filtro = [a for a in GRUPOS[grupo][1] if not a.startswith("--")]
    return [i for i in ids if not filtro or any(i.startswith(f) for f in filtro)]

_estado = {"rodando": False, "grupo": None, "inicio": None, "fim": None, "codigo": None}
_trava = threading.Lock()
_thread: threading.Thread | None = None
_LINHA = re.compile(r"^→ (\S+) \.\.\. (ok|FALHOU)(.*)$")


def _rodar(grupo: str) -> None:
    args = GRUPOS[grupo][1]
    try:
        with LOG.open("w", encoding="utf-8") as log:
            p = subprocess.run([sys.executable, "-u", "-m", "simulador", *args],
                               stdout=log, stderr=subprocess.STDOUT, text=True)
        _estado["codigo"] = p.returncode
    except Exception as e:  # nunca derruba o app
        LOG.write_text(f"Erro ao rodar o simulador: {e}\n", encoding="utf-8")
        _estado["codigo"] = -1
    finally:
        _estado["rodando"] = False
        _estado["fim"] = datetime.now().isoformat(timespec="seconds")


@router.post("/testes/rodar/{grupo}")
def rodar(grupo: str):
    if grupo not in GRUPOS:
        raise HTTPException(404, "Grupo de casos desconhecido")
    with _trava:
        if _estado["rodando"]:
            raise HTTPException(409, "Já tem uma rodada em andamento")
        LOG.parent.mkdir(exist_ok=True)
        AO_VIVO.unlink(missing_ok=True)  # a rodada nova começa sem os casos da anterior
        _estado.update(rodando=True, grupo=grupo, inicio=datetime.now().isoformat(timespec="seconds"),
                       fim=None, codigo=None)
        global _thread
        _thread = threading.Thread(target=_rodar, args=(grupo,), daemon=True, name="rodada-testes")
        _thread.start()
    return status()


@router.get("/testes/status")
def status():
    casos = []
    resumo = atual = None
    if LOG.exists():
        for linha in LOG.read_text(encoding="utf-8", errors="replace").splitlines():
            linha = linha.strip()
            m = _LINHA.match(linha)
            if m:
                casos.append({"id": m.group(1), "ok": m.group(2) == "ok", "detalhe": m.group(3).strip(" :")})
            elif linha.startswith("→ "):  # caso que ainda está rodando ("→ id ..." sem o resultado)
                atual = linha[2:].split(" ")[0]
            elif "casos passaram" in linha:
                resumo = linha
    grupo = _estado["grupo"]
    if not _estado["rodando"]:
        atual = None
    previstos = ids_do_grupo(grupo) if grupo else []
    feitos = {c["id"] for c in casos}
    return {**_estado, "rotulo": GRUPOS[grupo][0] if grupo else None, "casos": casos, "resumo": resumo, "atual": atual,
            "total": len(previstos) or len(casos), "faltam": [i for i in previstos if i not in feitos and i != atual],
            "agora": datetime.now().isoformat(timespec="seconds"),
            "parcial": grupo != "todos"}
