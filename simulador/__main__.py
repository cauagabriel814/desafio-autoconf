"""Simulador de conversas: roda os casos de casos.yaml contra o pipeline e gera um relatório.

    docker compose run --rm app python -m simulador            # todos os casos
    docker compose run --rm app python -m simulador 1 4 x2     # só alguns (prefixo do id)
"""

import json
import re
import sys
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from app import llm
from app.agente import prompts
from app.config import settings
from app.canais.payload import MensagemEntrada, Resultado
from app.db import repo
from app.db.seed import iniciar_banco
from app.pipeline import orquestrador

AQUI = Path(__file__).parent
SAIDA = Path("relatorios")
AO_VIVO = SAIDA / "ao_vivo.json"  # resultado parcial, regravado a cada caso (a tela lê enquanto roda)


def norm(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t or "") if unicodedata.category(c) != "Mn").lower()


def _msg(cliente: str, item) -> MensagemEntrada:
    base = dict(canal="terminal", cliente_id=cliente, message_id=uuid.uuid4().hex)
    if isinstance(item, str):
        return MensagemEntrada(**base, conteudo=item)
    return MensagemEntrada(**base, tipo=item.get("tipo", "texto"), conteudo=item.get("texto", ""),
                           transcricao_simulada=item.get("transcricao"))


def _rotulo(item) -> str:
    if isinstance(item, str):
        return item
    return f"[{item.get('tipo')}] transcrição simulada: \"{item.get('transcricao', '')}\""


def verificar(v: dict, res, lead: dict, ultimo_motivo: str | None = None,
              turnos: list | None = None) -> list[tuple[str, bool, str]]:
    """Devolve [(verificação, passou, detalhe)]."""
    resposta = norm(res.texto_resposta)
    ficha = lead["ficha"]
    transf = lead["transferencias"][-1] if lead["transferencias"] else None
    out = []
    for termo in v.get("cita", []):
        out.append((f"cita \"{termo}\"", norm(termo) in resposta, ""))
    for chave in ("cita_algum", "cita_algum_2"):
        if chave in v:
            ok = any(norm(t) in resposta for t in v[chave])
            out.append((f"cita algum de {v[chave]}", ok, ""))
    for termo in v.get("nao_cita", []):
        out.append((f"não cita \"{termo}\"", norm(termo) not in resposta, ""))
    if "transferiu" in v:
        ok = (transf is not None) == v["transferiu"]
        out.append((f"transferiu = {v['transferiu']}", ok, f"status do lead: {lead['status']}"))
    if "motivo" in v:
        real = transf["motivo"] if transf else None
        out.append((f"motivo = {v['motivo']}", real == v["motivo"], f"real: {real}"))
    for campo, esperado in (v.get("ficha") or {}).items():
        real = ficha.get(campo)
        ok = norm(str(real)) == norm(str(esperado)) if isinstance(esperado, str) else real == esperado
        out.append((f"ficha.{campo} = {esperado}", ok, f"real: {real}"))
    if "bloqueio_entrada" in v:
        esperado = v["bloqueio_entrada"]
        if isinstance(esperado, list):
            out.append((f"guardrail de entrada em {esperado}", res.bloqueio_entrada in esperado,
                        f"real: {res.bloqueio_entrada}"))
        else:
            esperado = esperado or None
            out.append((f"guardrail de entrada = {esperado or 'não bloqueou'}", res.bloqueio_entrada == esperado,
                        f"real: {res.bloqueio_entrada}"))
    if "resposta_audio" in v:
        ok = (res.tipo_resposta == "audio") == v["resposta_audio"]
        out.append((f"respondeu em áudio = {v['resposta_audio']}", ok, f"real: {res.tipo_resposta}"))
    for regra in v.get("no_turno", []):
        # a resposta do turno N (1 = primeira mensagem) precisa citar algum destes termos
        n = regra["turno"]
        r = (turnos or [])[n - 1][2] if turnos and len(turnos) >= n else None
        texto = norm(r.texto_resposta) if r else ""
        ok = any(norm(t) in texto for t in regra["cita_algum"])
        out.append((f"no turno {n}, cita algum de {regra['cita_algum']}", ok, f"real: {texto[:80]}"))
    for regra in v.get("depois_do_turno", []):
        # respostas do agente a partir do turno N (1 = primeira mensagem) não podem conter estes termos
        n = regra["turno"]
        posteriores = [norm(r.texto_resposta) for _, _, r in (turnos or [])[n:] if r]
        for termo in regra["nao_cita"]:
            ok = all(norm(termo) not in t for t in posteriores)
            out.append((f"depois do turno {n}, não pergunta de novo \"{termo}\"", ok, ""))
    if v.get("lista_em_ordem_de_preco"):
        # toda lista de carros do agente vem do mais barato ao mais caro
        ok, detalhe = True, ""
        for _, _, r in turnos or []:
            if not r:
                continue
            precos = [int(p.replace(".", "")) for p in re.findall(r"^\s*[-•].*R\$\s*([\d.]+)", r.texto_resposta, re.M)]
            if precos != sorted(precos):
                ok, detalhe = False, f"real: {precos}"
        out.append(("listas em ordem de preço (menor → maior)", ok, detalhe))
    if v.get("nao_repete_comentario"):
        # o comentário de vendedor de uma resposta não pode aparecer de novo numa resposta seguinte
        vistos, repetido = set(), ""
        for _, _, r in turnos or []:
            if not r:
                continue
            frases = {norm(f.strip()) for f in re.split(r"(?<=[.!?])\s+|\n", r.texto_resposta)
                      if f.strip() and not f.strip().startswith(("-", "•")) and not f.strip().endswith("?")
                      and re.search(r"\b(km|novinho|em conta|economic|barat|mais novo)\b", norm(f))}
            if frases & vistos:
                repetido = next(iter(frases & vistos))
            vistos |= frases
        out.append(("não repete o comentário de vendedor", not repetido, f"repetiu: {repetido}"))
    if "status" in v:
        out.append((f"status do lead = {v['status']}", lead["status"] == v["status"], f"real: {lead['status']}"))
    if "ultima_ignorada" in v:
        out.append((f"última mensagem sem resposta ({v['ultima_ignorada']})", ultimo_motivo == v["ultima_ignorada"],
                    f"real: {ultimo_motivo}"))
    return out


def rodar_caso(caso: dict) -> dict:
    cliente = f"sim-{caso['id']}-{uuid.uuid4().hex[:4]}"
    turnos = []
    res = None
    ultimo_motivo = None
    for item in caso["mensagens"]:
        msg = _msg(cliente, item)
        confirmacao = orquestrador.executar_comando(msg)
        if confirmacao is not None:
            ultimo_motivo = "comando"
            turnos.append((_rotulo(item), f"(comando) {confirmacao}", None))
            continue
        segue, motivo = orquestrador.aceitar(msg)
        ultimo_motivo = motivo
        if not segue:
            turnos.append((_rotulo(item), f"(sem resposta: {motivo})", None))
            continue
        res = orquestrador.processar([msg])
        turnos.append((_rotulo(item), res.texto_resposta or " / ".join(res.textos), res))
    res = res or Resultado()
    # Sempre o lead ATUAL do número (depois de um #reset é um lead novo)
    lead = repo.detalhe_lead(repo.obter_ou_criar_lead("terminal", cliente).id)
    checks = verificar(caso.get("verificar", {}), res, lead, ultimo_motivo, turnos)
    return {"caso": caso, "turnos": turnos, "checks": checks, "lead": lead, "ok": all(c[1] for c in checks)}


def relatorio(resultados: list[dict]) -> str:
    total = len(resultados)
    ok = sum(r["ok"] for r in resultados)
    linhas = [
        "# Relatório do simulador",
        "",
        f"Gerado em {datetime.now():%d/%m/%Y %H:%M}. **{ok} de {total} casos passaram.**",
        "",
        "| Caso | Esperado | Resultado |",
        "|---|---|---|",
    ]
    for r in resultados:
        c = r["caso"]
        falhas = [x[0] for x in r["checks"] if not x[1]]
        linhas.append(f"| `{c['id']}` | {c['esperado']} | {'✅ passou' if r['ok'] else '❌ ' + '; '.join(falhas)} |")
    linhas.append("")
    for r in resultados:
        c = r["caso"]
        linhas += ["---", "", f"## {'✅' if r['ok'] else '❌'} `{c['id']}` · {c['descricao']}", "",
                   f"**Esperado:** {c['esperado']}", "", "**Conversa**", ""]
        for cliente, agente, res in r["turnos"]:
            linhas.append(f"> **Cliente:** {cliente}  ")
            linhas.append(f"> **Agente:** {agente}")
            if res:
                info = []
                if res.tools_chamadas:
                    info.append("tools: " + ", ".join(res.tools_chamadas))
                if res.bloqueio_entrada:
                    info.append(f"guardrail de entrada: {res.bloqueio_entrada}")
                if res.bloqueios_saida:
                    info.append("guardrail de saída barrou: " + " | ".join(res.bloqueios_saida))
                if info:
                    linhas.append(f">  \n> _{' · '.join(info)}_")
            linhas.append(">")
        linhas += ["", "**Verificações**", ""]
        for nome, passou, det in r["checks"]:
            linhas.append(f"- {'✅' if passou else '❌'} {nome}{f' ({det})' if det and not passou else ''}")
        ficha = {k: v for k, v in r["lead"]["ficha"].items() if v is not None}
        if ficha:
            linhas += ["", f"**Ficha final:** `{ficha}`"]
        if r["lead"]["transferencias"]:
            t = r["lead"]["transferencias"][-1]
            linhas += ["", f"**Resumo para o vendedor** ({t['motivo']}, urgência {t['urgencia']}):", "",
                       *[f"> {l}" for l in t["resumo"].splitlines()]]
        linhas.append("")
    return "\n".join(linhas)


class NotaTom(BaseModel):
    nota: int = Field(ge=1, le=5)
    motivo: str


def avaliar_tom(caso: dict, r: dict) -> None:
    """Juiz de humanidade: nota de 1 a 5 para cada resposta do agente (só com --tom)."""
    juiz = llm.chat(temperature=0).with_structured_output(NotaTom)
    contexto: list[str] = []
    for i, (cliente, agente, res) in enumerate(r["turnos"]):
        if res and res.texto_resposta:
            v = juiz.invoke(prompts.JUIZ_TOM.format(contexto="\n".join(contexto[-6:]) or "(início)",
                                                    cliente=cliente, resposta=res.texto_resposta))
            r.setdefault("tom", []).append({"turno": i + 1, "nota": v.nota, "motivo": v.motivo,
                                            "cliente": cliente, "agente": res.texto_resposta})
        contexto += [f"Cliente: {cliente}", f"Atendente: {agente}"]


def secao_tom(resultados: list[dict]) -> list[str]:
    notas = [t for r in resultados for t in r.get("tom", [])]
    if not notas:
        return []
    media = sum(t["nota"] for t in notas) / len(notas)
    dist = {n: sum(1 for t in notas if t["nota"] == n) for n in range(5, 0, -1)}
    l = ["", "## Humanidade (juiz de tom)", "",
         f"**Média {media:.2f} de 5** em {len(notas)} respostas · "
         + " · ".join(f"{n}★: {q}" for n, q in dist.items()), "", "Piores respostas:", ""]
    for t in sorted(notas, key=lambda t: t["nota"])[:6]:
        l += [f"- **{t['nota']}★** · cliente: _{t['cliente']}_  ", f"  agente: {t['agente'][:220]}  ",
              f"  motivo: {t['motivo']}"]
    return l


def para_json(resultados: list[dict]) -> dict:
    """Mesmo conteúdo do relatório em Markdown, para a tela /painel/testes."""
    casos = []
    for r in resultados:
        c = r["caso"]
        turnos = []
        for cliente, agente, res in r["turnos"]:
            turnos.append({
                "cliente": cliente,
                "agente": agente,
                "tools": res.tools_chamadas if res else [],
                "bloqueio_entrada": res.bloqueio_entrada if res else None,
                "bloqueios_saida": res.bloqueios_saida if res else [],
                "audio": bool(res and res.tipo_resposta == "audio"),
            })
        t = r["lead"]["transferencias"][-1] if r["lead"]["transferencias"] else None
        casos.append({
            "id": c["id"],
            "descricao": c["descricao"],
            "esperado": c["esperado"],
            "ok": r["ok"],
            "turnos": turnos,
            "checks": [{"nome": n, "ok": ok, "detalhe": d} for n, ok, d in r["checks"]],
            "ficha": {k: v for k, v in r["lead"]["ficha"].items() if v is not None},
            "transferencia": {"motivo": t["motivo"], "urgencia": t["urgencia"], "resumo": t["resumo"]} if t else None,
            "tom": [{"turno": x["turno"], "nota": x["nota"], "motivo": x["motivo"]} for x in r.get("tom", [])],
        })
    notas = [x["nota"] for c in casos for x in c["tom"]]
    return {
        "gerado_em": datetime.now().isoformat(timespec="minutes"),
        "total": len(casos),
        "passaram": sum(c["ok"] for c in casos),
        "tom_media": round(sum(notas) / len(notas), 2) if notas else None,
        "tom_respostas": len(notas),
        "casos": casos,
    }


def main() -> None:
    # O simulador sempre roda em modo de teste: os casos de #reset/#parar precisam dos comandos ligados.
    settings.test = "on"
    iniciar_banco()
    casos = yaml.safe_load((AQUI / "casos.yaml").read_text(encoding="utf-8"))
    args = sys.argv[1:]
    tom = "--tom" in args  # liga o juiz de humanidade (1 chamada extra ao modelo por resposta)
    filtro = [a for a in args if not a.startswith("--")]
    if filtro:
        casos = [c for c in casos if any(c["id"].startswith(f) for f in filtro)]
    resultados = []
    SAIDA.mkdir(exist_ok=True)
    for caso in casos:
        print(f"→ {caso['id']} ...", end=" ", flush=True)
        r = rodar_caso(caso)
        if tom:
            avaliar_tom(caso, r)
        resultados.append(r)
        # A tela Testes mostra cada caso assim que ele termina
        tmp = AO_VIVO.with_suffix(".tmp")  # grava inteiro e troca: a tela nunca lê um arquivo pela metade
        tmp.write_text(json.dumps(para_json(resultados), ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(AO_VIVO)
        notas = [t["nota"] for t in r.get("tom", [])]
        extra = f"  [tom {sum(notas) / len(notas):.1f}]" if notas else ""
        print(("ok" if r["ok"] else "FALHOU: " + "; ".join(x[0] for x in r["checks"] if not x[1])) + extra)
    llm.flush()
    SAIDA.mkdir(exist_ok=True)
    # Rodada completa vai para resultado.md (o que entra no repositório); parcial não sobrescreve
    destino = SAIDA / ("resultado_parcial.md" if filtro else "resultado.md")
    destino.write_text(relatorio(resultados) + "\n".join(secao_tom(resultados)) + "\n", encoding="utf-8")
    if tom:
        todas = [t["nota"] for r in resultados for t in r.get("tom", [])]
        print(f"Humanidade: média {sum(todas) / len(todas):.2f} de 5 em {len(todas)} respostas")
    # A tela mostra a rodada completa (resultado.json) e a última parcial (resultado_parcial.json)
    (SAIDA / ("resultado_parcial.json" if filtro else "resultado.json")).write_text(
        json.dumps(para_json(resultados), ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    print(f"\n{sum(r['ok'] for r in resultados)}/{len(resultados)} casos passaram. Relatório: {destino}")


if __name__ == "__main__":
    main()
