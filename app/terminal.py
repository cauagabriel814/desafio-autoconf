"""Canal terminal: conversa com o agente direto pelo terminal.

    docker compose run --rm app python -m app.terminal
"""

import uuid

from app.canais.payload import MensagemEntrada
from app.config import settings
from app.db.seed import iniciar_banco
from app.pipeline import orquestrador


def main() -> None:
    iniciar_banco()
    cliente = f"term-{uuid.uuid4().hex[:6]}"
    print(f"Conversa {cliente}. Digite 'sair' para encerrar.\n")
    while True:
        try:
            texto = input("você > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if texto.lower() in ("sair", "exit", "quit"):
            break
        if not texto:
            continue
        msg = MensagemEntrada(canal="terminal", cliente_id=cliente, message_id=uuid.uuid4().hex, conteudo=texto)
        confirmacao = orquestrador.executar_comando(msg)
        if confirmacao is not None:
            print(f"   [comando] {confirmacao}\n")
            continue
        segue, motivo = orquestrador.aceitar(msg)
        if not segue:
            print(f"   (sem resposta: {motivo})\n")
            continue
        res = orquestrador.processar([msg])
        for t in res.textos:
            print(f"{settings.nome_atendente.lower():<4} > {t}")
        extras = []
        if res.tools_chamadas:
            extras.append("tools: " + ", ".join(res.tools_chamadas))
        if res.bloqueio_entrada:
            extras.append(f"guardrail de entrada: {res.bloqueio_entrada}")
        if res.bloqueios_saida:
            extras.append("guardrail de saída: " + " | ".join(res.bloqueios_saida))
        if res.transferido:
            extras.append("TRANSFERIDO PARA VENDEDOR")
        if extras:
            print("       [" + " · ".join(extras) + "]")
        print()


if __name__ == "__main__":
    main()
