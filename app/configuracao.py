"""Configuração do agente editável pela tela /painel/config e pela API /config.

O que dá para mudar fica no banco e vale por cima do .env, na hora, sem reiniciar.
Modelos, endereços e chaves ficam só para consulta (mudam no .env).
"""

import platform
from importlib import metadata

from sqlalchemy import delete, select

from app.config import settings
from app.db.modelos import Configuracao
from app.db.sessao import SessionLocal

VOZES = ["alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer", "verse"]
MODOS_RESPOSTA = {
    "espelhar": "Responde no mesmo formato que o cliente mandou (áudio → áudio, texto/imagem → texto)",
    "sempre_texto": "Sempre responde em texto, mesmo quando o cliente manda áudio",
    "sempre_audio": "Sempre responde em áudio (a lista de carros vai junto por escrito)",
    "texto_e_audio": "Responde em áudio e também manda o texto completo",
}

# chave -> (tipo, rótulo, ajuda, opções ou (mín, máx))
EDITAVEIS: dict[str, tuple] = {
    "modo_resposta": ("opcao", "Quando responder em áudio", "Regra para escolher entre áudio e texto.",
                      list(MODOS_RESPOSTA)),
    "openai_tts_voice": ("opcao", "Voz do áudio", "Voz usada quando o agente responde em áudio.", VOZES),
    "nome_atendente": ("texto", "Nome da atendente", "Como o agente se apresenta.", (1, 40)),
    "nome_loja": ("texto", "Nome da loja", "Nome da revenda nas mensagens.", (1, 60)),
    "trigger_keyword": ("texto", "Palavra-chave do WhatsApp", "Sem ela o bot não responde no WhatsApp.", (2, 30)),
    "aceitar_proprio_numero": ("opcao", "Atender o próprio número",
                               "Para testar com um número só: no WhatsApp, abra \"Mensagem para você\" e mande "
                               "#thread. Desligado, o bot ignora o próprio número.", ["on", "off"]),
    "test": ("opcao", "Modo de teste", "Liga os comandos de teste no WhatsApp.", ["on", "off"]),
    "reset_keyword": ("texto", "Comando para recomeçar", "Arquiva a conversa (só com modo de teste).", (2, 30)),
    "stop_keyword": ("texto", "Comando para parar", "O agente para de responder (só com modo de teste).", (2, 30)),
    "agrupar_segundos": ("numero", "Espera para agrupar mensagens (s)",
                         "Junta mensagens seguidas do WhatsApp antes de responder.", (0, 30)),
    "sessao_minutos": ("inteiro", "Sessão do #thread (min)",
                       "Por quanto tempo áudio e imagem passam sem a palavra-chave.", (1, 1440)),
    "historico_n": ("inteiro", "Mensagens de histórico", "Quantas mensagens o agente lê da conversa.", (4, 60)),
}

PADROES = {k: getattr(settings, k) for k in EDITAVEIS}  # valores do .env (para restaurar)

SO_CONSULTA = [
    ("Modelo de conversa (LLM)", "openai_model"),
    ("Modelo de transcrição", "openai_stt_model"),
    ("Modelo de voz (TTS)", "openai_tts_model"),
    ("Evolution API", "evolution_api_url"),
    ("Instância do WhatsApp", "evolution_instance"),
    ("Langfuse", "langfuse_host"),
    ("Banco de dados", "database_url"),
]

BIBLIOTECAS = ["fastapi", "uvicorn", "pydantic", "pydantic-settings", "sqlalchemy", "langchain", "langchain-core",
               "langchain-openai", "langgraph", "openai", "langfuse", "httpx", "jinja2", "pyyaml", "pytest"]


class ValorInvalido(ValueError):
    pass


def _converter(chave: str, valor):
    tipo, _, _, regra = EDITAVEIS[chave]
    if tipo == "opcao":
        v = str(valor).strip()
        if v not in regra:
            raise ValorInvalido(f"{chave}: use um de {', '.join(regra)}")
        return v
    if tipo == "texto":
        v = str(valor).strip()
        if not regra[0] <= len(v) <= regra[1]:
            raise ValorInvalido(f"{chave}: entre {regra[0]} e {regra[1]} caracteres")
        return v
    try:
        v = int(valor) if tipo == "inteiro" else float(valor)
    except (TypeError, ValueError):
        raise ValorInvalido(f"{chave}: precisa ser um número")
    if not regra[0] <= v <= regra[1]:
        raise ValorInvalido(f"{chave}: entre {regra[0]} e {regra[1]}")
    return v


def carregar() -> None:
    """Aplica no settings o que foi salvo pela tela (chamado na subida do app)."""
    with SessionLocal() as s:
        for c in s.scalars(select(Configuracao)).all():
            if c.chave in EDITAVEIS:
                try:
                    setattr(settings, c.chave, _converter(c.chave, c.valor.get("v")))
                except ValorInvalido:
                    pass


def salvar(dados: dict) -> dict:
    """Valida tudo antes de gravar (ou grava tudo ou nada) e aplica na hora."""
    desconhecidas = [k for k in dados if k not in EDITAVEIS]
    if desconhecidas:
        raise ValorInvalido(f"não dá para mudar por aqui: {', '.join(desconhecidas)}")
    novos = {k: _converter(k, v) for k, v in dados.items()}
    with SessionLocal() as s:
        for k, v in novos.items():
            c = s.get(Configuracao, k) or Configuracao(chave=k)
            c.valor = {"v": v}
            s.add(c)
        s.commit()
    for k, v in novos.items():
        setattr(settings, k, v)
    return atual()


def restaurar() -> dict:
    """Volta tudo para o .env."""
    with SessionLocal() as s:
        s.execute(delete(Configuracao))
        s.commit()
    for k, v in PADROES.items():
        setattr(settings, k, v)
    return atual()


def _versao(pacote: str) -> str:
    try:
        return metadata.version(pacote)
    except metadata.PackageNotFoundError:
        return "não instalado"


def atual() -> dict:
    editaveis = []
    for k, (tipo, rotulo, ajuda, regra) in EDITAVEIS.items():
        editaveis.append({
            "chave": k, "tipo": tipo, "rotulo": rotulo, "ajuda": ajuda, "valor": getattr(settings, k),
            "padrao": PADROES[k], "opcoes": regra if tipo == "opcao" else None,
            "limites": regra if tipo in ("texto", "numero", "inteiro") else None,
        })
    return {
        "editaveis": editaveis,
        "modos_resposta": MODOS_RESPOSTA,
        "so_consulta": [{"rotulo": r, "chave": k, "valor": getattr(settings, k)} for r, k in SO_CONSULTA],
        "chaves": {
            "OPENAI_API_KEY": bool(settings.openai_api_key),
            "EVOLUTION_API_KEY": bool(settings.evolution_api_key),
            "LANGFUSE": settings.langfuse_ativo,
        },
        "bibliotecas": [{"nome": b, "versao": _versao(b)} for b in BIBLIOTECAS],
        "python": platform.python_version(),
    }
