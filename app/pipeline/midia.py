"""Etapa 03: transforma áudio e imagem em texto."""

import base64
import logging

from app import llm
from app.agente import prompts
from app.config import settings

log = logging.getLogger(__name__)

# Formatos que a OpenAI aceita (transcrição e visão). O resto recebe uma resposta amigável, sem quebrar.
_EXT = {
    "audio/ogg": "ogg", "audio/oga": "ogg", "audio/opus": "ogg", "audio/webm": "webm", "video/webm": "webm",
    "audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/mp4": "m4a", "audio/x-m4a": "m4a", "audio/m4a": "m4a",
    "audio/wav": "wav", "audio/x-wav": "wav", "audio/wave": "wav", "audio/flac": "flac", "audio/x-flac": "flac",
}
IMAGENS = {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif"}
MAX_AUDIO = 25 * 1024 * 1024  # limite da transcrição
MAX_IMAGEM = 20 * 1024 * 1024


def tipo_suportado(mime: str | None, tamanho: int) -> tuple[str, str | None]:
    """('audio' | 'imagem' | 'outro', motivo de não suportar)."""
    base = (mime or "").split(";")[0].strip().lower()
    if base in _EXT:
        if tamanho > MAX_AUDIO:
            return "outro", "áudio maior que 25 MB"
        return "audio", None
    if base in IMAGENS:
        if tamanho > MAX_IMAGEM:
            return "outro", "imagem maior que 20 MB"
        return "imagem", None
    if base.startswith("audio/"):
        return "outro", f"formato de áudio não suportado ({base})"
    if base.startswith("image/"):
        return "outro", f"formato de imagem não suportado ({base})"
    return "outro", f"tipo de arquivo não suportado ({base or 'desconhecido'})"


# Erros comuns da transcrição com nomes de marca/modelo falados em português.
# Aplicado em código depois da transcrição (e no texto digitado, que também vem com "biuaidi" etc.).
_ALIASES = [
    (r"\bbuild\s*i\.?\s*d\b|\bbi\s*-?\s*wai\s*-?\s*di\b|\bbi\s*-?\s*uai\s*-?\s*di\b|\bbiwaidi\b|\bbiuaidi\b"
     r"|\bbai\s*-?\s*di\b|\bbaidi\b|\bb\s*y\s*d\b|\bbê\s*ípsilon\s*dê\b|\bby\s+de\b", "BYD"),
    (r"\bv[eê]\s*d[aá]bli[uo]\b|\bv[eê]\s*dabl[ií]u\b|\bfolks?\b|\bf[oó]lquis\b|\bvolks\b"
     r"|\bvolkswagen\b|\bwv\b|\bw\s+v\b", "VW"),
    (r"\bag[aá]\s*b[eê]\s*vinte\b|\bh\s*b\s*vinte\b|\bh\s*b\s*20\b", "HB20"),
    (r"\bt\s*cross\b|\bti\s*cross\b|\bt[eê]\s*cross\b|\btecross\b", "T-Cross"),
    (r"\bag[aá]\s*erre\s*v[eê]\b|\bhr\s*v\b", "HR-V"),
    (r"\bs\s*dez\b|\besse\s*dez\b|\bs\s*10\b", "S10"),
    (r"\bchevrol[eéê]\b|\bchevr[ôo]le\b|\bchevrol[eéê]t\b", "Chevrolet"),
    (r"\bonyx\b|\b[ôo]nix\b", "Onix"),
    (r"\bcompas\b", "Compass"),
    (r"\bkiks\b|\bquicks\b|\bquics\b", "Kicks"),
]


# Só para áudio: palavras reais que, numa loja de carros, quase sempre são a marca mal transcrita.
_ALIASES_AUDIO = [
    (r"\bbiotipos?\b|\bbiotip\w*\b|\bbaiti\b|\bbaid\b", "BYD"),
]


def _sem_acento(t: str) -> str:
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn").lower()


def _vocabulario_de_carros() -> dict[str, str]:
    """Palavras de marca e modelo (sem acento, minúsculas) -> como escrever. Só as de 5 letras ou mais."""
    from app.catalogo import CATALOGO
    from app.db import repo

    palavras: dict[str, str] = {}
    for c in repo.buscar_carros(apenas_disponiveis=False):
        for p in (c["marca"], c["modelo"].split()[0]):
            palavras[_sem_acento(p)] = p
    for marca, modelos in CATALOGO.items():
        palavras[_sem_acento(marca)] = marca
        for mod in modelos:
            palavras[_sem_acento(mod.split()[0])] = mod.split()[0]
    return {k: v for k, v in palavras.items() if len(k) >= 5 and k.isalpha()}


def corrigir_digitacao(texto: str) -> str:
    """Marca ou modelo escrito com erro ("hiunday", "toyta", "corola") vira o nome certo do estoque.

    Só palavras de 5+ letras e bem parecidas (85%) com uma marca ou modelo real, para não mexer em português comum.
    """
    import difflib
    import re

    vocab = _vocabulario_de_carros()
    if not vocab:
        return texto
    chaves = list(vocab)

    def troca(m: re.Match) -> str:
        palavra = m.group(0)
        chave = _sem_acento(palavra)
        if len(chave) < 5 or chave in vocab:
            return palavra
        achado = difflib.get_close_matches(chave, chaves, n=1, cutoff=0.85)
        return vocab[achado[0]] if achado else palavra

    return re.sub(r"[A-Za-zÀ-ú]+", troca, texto)


def normalizar_nomes(texto: str, audio: bool = False) -> str:
    import re

    for padrao, certo in _ALIASES + (_ALIASES_AUDIO if audio else []):
        texto = re.sub(padrao, certo, texto, flags=re.IGNORECASE)
    try:
        return corrigir_digitacao(texto)
    except Exception:  # a correção é um extra: nunca derruba o atendimento
        return texto


def vocabulario() -> str:
    """Dica de contexto para a transcrição: marcas e modelos do estoque e do catálogo."""
    from app.catalogo import CATALOGO
    from app.db import repo

    # A transcrição só usa ~220 tokens da dica: marcas + nomes curtos de modelo, estoque primeiro.
    estoque = repo.buscar_carros(apenas_disponiveis=False)
    marcas = sorted({c["marca"] for c in estoque} | set(CATALOGO))
    modelos: list[str] = []
    curtos = [c["modelo"].split()[0] for c in estoque]
    curtos += [mod for mods in CATALOGO.values() for mod in mods if " " not in mod]
    for m in curtos:
        if m not in modelos:
            modelos.append(m)
    texto = ("Revenda de carros seminovos. Marcas: " + ", ".join(marcas)
             + ". Modelos: " + ", ".join(modelos))
    return texto[:700]


def transcrever(audio_b64: str, mime: str | None) -> str:
    """Transcreve o áudio. Devolve "" se não deu para entender."""
    mime_base = (mime or "audio/ogg").split(";")[0].strip()
    ext = _EXT.get(mime_base, "ogg")
    dados = base64.b64decode(audio_b64)
    if len(dados) < 1000:  # arquivo vazio ou cortado
        return ""
    try:
        r = llm.openai_client().audio.transcriptions.create(
            model=settings.openai_stt_model,
            file=(f"audio.{ext}", dados, mime_base),
            language="pt",
            prompt=vocabulario(),  # ajuda a acertar "BYD", "T-Cross", "HB20"...
        )
        texto = normalizar_nomes((r.text or "").strip(), audio=True)
    except llm.SemChaveOpenAI:
        raise
    except Exception:
        log.exception("Falha na transcrição")
        return ""
    # Transcrição com quase nada de conteúdo = áudio com ruído
    if len(texto.split()) < 2 and not any(c.isdigit() for c in texto):
        return ""
    return texto


def descrever_imagem(img_b64: str, mime: str | None, legenda: str = "") -> str:
    url = f"data:{mime or 'image/jpeg'};base64,{img_b64}"
    msg = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompts.DESCREVER_IMAGEM},
                {"type": "image_url", "image_url": {"url": url}},
            ],
        }
    ]
    try:
        r = llm.chat(temperature=0).invoke(msg)
        desc = r.content.strip()
    except llm.SemChaveOpenAI:
        raise
    except Exception:
        log.exception("Falha ao descrever imagem")
        desc = "não foi possível ver a imagem"
    texto = f"[Cliente enviou uma imagem: {desc}]"
    if legenda:
        texto += f" {legenda}"
    return texto
