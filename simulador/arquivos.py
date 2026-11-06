"""Teste de todos os tipos de arquivo pelo /chat (como a tela envia), com o modelo de verdade.

    docker compose exec app python -m simulador.arquivos

Gera relatorios/arquivos.md. Áudios são gerados pela voz da OpenAI; imagens com o Pillow.
"""

import io
import os
import uuid
from datetime import datetime
from pathlib import Path

import httpx

from app import llm
from app.agente import prompts
from app.config import settings

URL = os.environ.get("CHAT_URL", "http://localhost:8000/chat")
FRASE = "Oi, eu quero um SUV até cem mil reais."
AMIGAVEL = prompts.FIXAS["arquivo_nao_suportado"]
ERRO = prompts.FIXAS["erro"]


def audio(formato: str) -> bytes:
    buf = io.BytesIO()
    with llm.openai_client().audio.speech.with_streaming_response.create(
        model=settings.openai_tts_model, voice="ash", input=FRASE, response_format=formato
    ) as r:
        for ch in r.iter_bytes():
            buf.write(ch)
    return buf.getvalue()


def imagem(formato: str) -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (640, 360), (230, 236, 240))
    d = ImageDraw.Draw(img)
    d.rectangle([120, 160, 520, 260], fill=(30, 90, 160))  # "carro" simples
    d.ellipse([170, 230, 250, 310], fill=(20, 20, 20))
    d.ellipse([390, 230, 470, 310], fill=(20, 20, 20))
    d.text((140, 60), "SUV azul - quero um parecido", fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format=formato)
    return buf.getvalue()


def casos() -> list[tuple[str, str, bytes, str, str]]:
    """(nome, mime, bytes, nome do arquivo, esperado: transcreve | imagem | amigavel)"""
    c = []
    for fmt, mime, ext in (("mp3", "audio/mpeg", "mp3"), ("opus", "audio/ogg", "ogg"), ("wav", "audio/wav", "wav"),
                           ("flac", "audio/flac", "flac")):
        c.append((f"Áudio {ext}", mime, audio(fmt), f"a.{ext}", "transcreve"))
    c.append(("Áudio aac (não suportado)", "audio/aac", audio("aac"), "a.aac", "amigavel"))
    for fmt, mime in (("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp"), ("GIF", "image/gif")):
        c.append((f"Imagem {fmt.lower()}", mime, imagem(fmt), f"i.{fmt.lower()}", "imagem"))
    c += [
        ("Imagem HEIC (iPhone)", "image/heic", b"\x00\x00\x00\x18ftypheic" + b"\x00" * 4000, "i.heic", "amigavel"),
        ("Imagem > 20 MB", "image/png", b"\x89PNG" + b"\x00" * (21 * 1024 * 1024), "grande.png", "amigavel"),
        ("PDF", "application/pdf", b"%PDF-1.4\n" + b"0" * 3000, "doc.pdf", "amigavel"),
        ("TXT", "text/plain", "quero um carro".encode(), "nota.txt", "amigavel"),
        ("DOCX", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", b"PK" + b"0" * 3000,
         "doc.docx", "amigavel"),
        ("Vídeo MP4", "video/mp4", b"\x00\x00\x00\x18ftypmp42" + b"0" * 5000, "v.mp4", "amigavel"),
        ("Arquivo vazio", "audio/ogg", b"", "vazio.ogg", "amigavel"),
    ]
    return c


def enviar(nome_arq: str, mime: str, dados: bytes) -> dict:
    r = httpx.post(URL, data={"cliente_id": f"web-arq-{uuid.uuid4().hex[:6]}"},
                   files={"arquivo": (nome_arq, dados, mime)}, timeout=180)
    return {"http": r.status_code, **(r.json() if r.headers.get("content-type", "").startswith("application/json") else {})}


def avaliar(esperado: str, r: dict) -> tuple[bool, str]:
    textos = " ".join(r.get("textos") or [])
    if r["http"] != 200:
        return False, f"HTTP {r['http']}"
    if any(e in textos for e in ERRO):
        return False, "deu erro no pipeline"
    if esperado == "amigavel":
        return any(a[:30] in textos for a in AMIGAVEL), "resposta amigável" if textos else "sem resposta"
    entendido = (r.get("texto_cliente") or "").lower()
    respondeu = bool(textos or r.get("audio_url"))
    if esperado == "transcreve":
        return ("suv" in entendido and respondeu), f"transcrito: {entendido[:60]!r}"
    return (entendido.startswith("[cliente enviou uma imagem") and respondeu), f"visto: {entendido[:80]!r}"


def main() -> None:
    linhas, ok = [], 0
    lista = casos()
    for nome, mime, dados, arq, esperado in lista:
        r = enviar(arq, mime, dados)
        passou, det = avaliar(esperado, r)
        ok += passou
        resposta = (" / ".join(r.get("textos") or []) or ("[áudio]" if r.get("audio_url") else "-"))[:160]
        print(f"{'✅' if passou else '❌'} {nome:<26} {det}")
        linhas.append(f"| {'✅' if passou else '❌'} | {nome} | `{mime}` | {len(dados):,} | {esperado} | {det} | "
                      f"{resposta.replace('|', '/').replace(chr(10), ' ')} |".replace(",", "."))
    Path("relatorios").mkdir(exist_ok=True)
    Path("relatorios/arquivos.md").write_text(
        "# Tipos de arquivo\n\n"
        f"Gerado em {datetime.now():%d/%m/%Y %H:%M}. **{ok} de {len(lista)} como esperado.**\n\n"
        "| | Arquivo | Tipo | Bytes | Esperado | Resultado | Resposta |\n|---|---|---|---|---|---|---|\n"
        + "\n".join(linhas) + "\n", encoding="utf-8")
    print(f"\n{ok}/{len(lista)} como esperado. Relatório: relatorios/arquivos.md")


if __name__ == "__main__":
    main()
