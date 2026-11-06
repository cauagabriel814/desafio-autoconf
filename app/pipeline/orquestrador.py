"""Liga as etapas 02 a 07. Os canais chamam `processar` com a(s) mensagem(ns) já agrupada(s)."""

import logging
import re

from app import llm
from app.agente import prompts
from app.agente.grafo import rodar_agente
from app.canais.payload import MensagemEntrada, Resultado
from app.config import settings
from app.db import repo
from app.pipeline import filtros, guard_entrada, guard_saida, midia, resposta

log = logging.getLogger(__name__)

# Mensagem que é só cumprimento (sem pedido): "oi", "boa tarde", "olá, tudo bem?", "e aí", "#thread" sozinho
_SO_CUMPRIMENTO = re.compile(
    r"^(oi+|ol[aá]|opa|e a[ií]|eai|eae|bom dia|boa tarde|boa noite|hey|hello|salve|fala|tudo bem|tudo bom)"
    r"([\s,!.?]+(oi+|ol[aá]|tudo (bem|bom|certo|tranquilo)|como vai|bom dia|boa tarde|boa noite|pessoal|lia|"
    r"tudo bem com (voc[eê]|vc)))*[\s,!.?😊🙂👋]*$",
    re.IGNORECASE,
)


CANAIS_COM_COMANDO = ("whatsapp", "terminal")  # na tela de chat os comandos não valem


def executar_comando(msg: MensagemEntrada) -> str | None:
    """#reset e #parar (modo de teste). Só com TEST=on, no WhatsApp e no terminal/simulador, sem #thread.

    Devolve o texto de confirmação, ou None se a mensagem não é um comando ativo.
    """
    if not settings.comandos_ativos or msg.canal not in CANAIS_COM_COMANDO or msg.tipo != "texto":
        return None
    cmd = filtros.comando(msg.conteudo)
    if not cmd:
        return None
    if filtros.duplicada(msg.message_id):
        return ""  # comando repetido pelo webhook: não confirma de novo
    lead = repo.obter_ou_criar_lead(msg.canal, msg.cliente_id, msg.nome)
    repo.salvar_mensagem(lead.id, "cliente", msg.conteudo.strip(), "texto", status="ignorada",
                         message_id=msg.message_id)
    if cmd == "reset":
        repo.arquivar_lead(lead.id)
        log.info("Lead %s arquivado por %s", lead.id, msg.conteudo.strip())
        gatilho = f", mandando {settings.trigger_keyword}" if msg.canal == "whatsapp" and settings.trigger_keyword else ""
        return prompts.COMANDO_RESET.format(gatilho_txt=gatilho)
    repo.definir_status(lead.id, "parado")
    log.info("Lead %s parado por %s", lead.id, msg.conteudo.strip())
    return prompts.COMANDO_PARAR.format(gatilho=settings.trigger_keyword or "uma mensagem")


def aceitar(msg: MensagemEntrada) -> tuple[bool, str | None]:
    """Etapa 02, passos 1 a 3. Devolve (segue?, motivo de parar)."""
    if filtros.duplicada(msg.message_id):
        return False, "duplicada"
    lead = repo.obter_ou_criar_lead(msg.canal, msg.cliente_id, msg.nome)
    if filtros.parado(lead):
        # Depois do #parar, só o #thread religa o agente (na mesma conversa)
        if msg.tipo == "texto" and filtros.contem_gatilho(msg.conteudo):
            repo.definir_status(lead.id, "bot")
            lead = repo.obter_lead(lead.id)
            log.info("Lead %s religado por %s", lead.id, settings.trigger_keyword)
        else:
            repo.salvar_mensagem(
                lead.id, "cliente", _texto_bruto(msg), msg.tipo, status="ignorada", message_id=msg.message_id
            )
            return False, "parado"
    if not filtros.checar_gatilho(msg, lead):
        return False, "sem_gatilho"
    if filtros.com_humano(lead):
        repo.salvar_mensagem(
            lead.id, "cliente", _texto_bruto(msg), msg.tipo, status="ignorada", message_id=msg.message_id
        )
        return False, "com_humano"
    return True, None


def _texto_bruto(msg: MensagemEntrada) -> str:
    if msg.tipo == "texto":
        return filtros.remover_gatilho(msg.conteudo)
    return f"[{msg.tipo}] {filtros.remover_gatilho(msg.conteudo)}".strip()


def _normalizar(msgs: list[MensagemEntrada]) -> tuple[str, str, bool]:
    """Etapa 03. Devolve (texto, tipo_origem, algum_audio_ruim)."""
    partes: list[str] = []
    audio_ruim = False
    for m in msgs:
        if m.tipo == "texto":
            limpo = filtros.remover_gatilho(m.conteudo)
            if not limpo and filtros.contem_gatilho(m.conteudo):
                limpo = "Oi"  # "#thread" sozinho = cliente chamando o atendimento
            partes.append(limpo)
        elif m.tipo == "audio":
            if m.transcricao_simulada is not None:
                t = m.transcricao_simulada
            elif m.midia_b64:
                t = midia.transcrever(m.midia_b64, m.midia_mime)
            else:
                t = ""
            if t.strip():
                partes.append(midia.normalizar_nomes(t, audio=True))
            else:
                audio_ruim = True
        elif m.tipo == "imagem":
            legenda = filtros.remover_gatilho(m.conteudo)
            if m.midia_b64:
                partes.append(midia.descrever_imagem(m.midia_b64, m.midia_mime, legenda))
            elif legenda:
                partes.append(legenda)
        elif m.tipo == "outro":
            legenda = filtros.remover_gatilho(m.conteudo)
            if legenda:  # documento/vídeo com legenda: a legenda vale como mensagem
                partes.append(f"[Cliente enviou um arquivo que não dá para abrir] {legenda}")
    tipos = {m.tipo for m in msgs}
    tipo_origem = "audio" if "audio" in tipos else ("imagem" if "imagem" in tipos else "texto")
    # nomes de marca/modelo escritos ou transcritos errado ("build ID" -> BYD, "agá bê vinte" -> HB20)
    texto = midia.normalizar_nomes("\n".join(p for p in partes if p.strip()))
    return texto, tipo_origem, audio_ruim


def _contexto(lead_id: int) -> str:
    hist = repo.historico(lead_id, 5)[:-1]
    return "\n".join(f"{'Cliente' if m.papel == 'cliente' else 'Atendente'}: {m.texto}" for m in hist)


def responder_em_audio(tipo_origem: str) -> bool:
    """Regra configurável em /painel/config (modo_resposta)."""
    modo = settings.modo_resposta
    if modo == "sempre_texto":
        return False
    if modo in ("sempre_audio", "texto_e_audio"):
        return True
    return tipo_origem == "audio"  # espelhar


def _entregar(lead_id: int, texto: str, tipo_origem: str, cb: list, res: Resultado) -> Resultado:
    """Etapa 07: formato pela regra de áudio/texto, grava e devolve para o canal enviar."""
    audio_nome = None
    if responder_em_audio(tipo_origem):
        try:
            falado = resposta.versao_para_audio(texto, cb)
            audio_nome = resposta.gerar_audio(falado)
        except Exception:
            log.exception("Falha ao preparar áudio")
    repo.salvar_mensagem(lead_id, "agente", texto, tipo_origem, status="enviada", audio_path=audio_nome)
    lead = repo.obter_lead(lead_id)
    res.status_lead = lead.status
    res.texto_resposta = texto
    if audio_nome:
        res.tipo_resposta = "audio"
        res.audio_path = audio_nome
        texto_junto = settings.modo_resposta == "texto_e_audio" or resposta.tem_detalhes(texto)
        res.textos = resposta.em_baloes(texto) if texto_junto else []
    else:
        res.tipo_resposta = "texto"
        res.textos = resposta.em_baloes(texto)  # 2-3 balões, como uma pessoa escreve no WhatsApp
    return res


def processar(msgs: list[MensagemEntrada]) -> Resultado:
    """Etapas 03 a 07 para um lote de mensagens do mesmo cliente (já aceitas pelos filtros)."""
    primeira = msgs[0]
    lead = repo.obter_ou_criar_lead(primeira.canal, primeira.cliente_id, primeira.nome)
    res = Resultado(lead_id=lead.id)
    rastreio = llm.Rastreio(lead.id, primeira.cliente_id, primeira.canal)
    try:
        res = _processar(msgs, lead, res, rastreio.callbacks, rastreio)
    finally:
        rastreio.fim(
            res.texto_cliente,
            res.texto_resposta or " / ".join(res.textos),
            tools=res.tools_chamadas,
            transferido=res.transferido,
            bloqueio_entrada=res.bloqueio_entrada,
            bloqueios_saida=res.bloqueios_saida,
            tipo_resposta=res.tipo_resposta,
            status_lead=res.status_lead,
        )
    return res


def _passar_agressivo(lead_id: int, tipo_origem: str, cb: list, res: Resultado) -> Resultado:
    """Segunda ofensa da conversa: em vez de insistir, passa para um vendedor (a assistente já pediu calma uma vez)."""
    from app.agente.ficha import MOTIVO_PARA_VENDEDOR
    from app.agente.tools import gerar_resumo

    try:
        resumo = gerar_resumo(lead_id, "agressivo", cb)
    except Exception:
        log.exception("Falha ao gerar resumo da transferência por ofensa")
        resumo = f"Motivo da transferência: {MOTIVO_PARA_VENDEDOR['agressivo']}"
    repo.registrar_transferencia(lead_id, "agressivo", "media", resumo)
    res.bloqueio_entrada = "ofensivo"
    res.transferido = True
    return _entregar(lead_id, prompts.fixa("agressivo"), tipo_origem, cb, res)


def _processar(msgs: list[MensagemEntrada], lead, res: Resultado, cb: list, rastreio) -> Resultado:
    primeira = msgs[0]
    try:
        # Etapa 03
        texto, tipo_origem, audio_ruim = _normalizar(msgs)
        if not texto and any(m.tipo == "outro" for m in msgs):
            # documento, vídeo, figurinha, formato não suportado: não quebra e responde com jeito
            repo.salvar_mensagem(lead.id, "cliente", "[arquivo que não dá para abrir]", "texto",
                                 message_id=primeira.message_id)
            res.texto_cliente = "[arquivo não suportado]"
            return _entregar(lead.id, prompts.fixa("arquivo_nao_suportado"), "texto", cb, res)
        if not texto:
            if audio_ruim:
                repo.salvar_mensagem(lead.id, "cliente", "[áudio inaudível]", "audio", message_id=primeira.message_id)
                return _entregar(lead.id, prompts.fixa("audio_ruim"), "texto", cb, res)
            res.ignorado = "vazia"
            res.tipo_resposta = "nenhuma"
            return res
        res.texto_cliente = texto
        repo.salvar_mensagem(lead.id, "cliente", texto, tipo_origem, message_id=primeira.message_id)

        # Cliente só cumprimentou e é o começo da conversa: apresenta e pergunta como pode ajudar (modelo fixo)
        if _SO_CUMPRIMENTO.match(texto.strip()) and not any(m.papel == "agente" for m in repo.historico(lead.id, 20)):
            return _entregar(lead.id, prompts.saudacao(texto), tipo_origem, cb, res)

        # Etapa 04
        classe = guard_entrada.classificar(texto, _contexto(lead.id), cb)
        rastreio.evento("guardrail_entrada", categoria=classe.categoria, motivo=classe.motivo)
        if classe.categoria != "permitido":
            # Áudio curto que "parece" fora do assunto quase sempre é transcrição ruim ("um biotipo" = "um BYD"):
            # pede para repetir em vez de dizer que não pode ajudar.
            if tipo_origem == "audio" and classe.categoria == "fora_do_assunto" and len(texto.split()) <= 5:
                res.bloqueio_entrada = "audio_incerto"
                return _entregar(lead.id, prompts.fixa("audio_incerto"), tipo_origem, cb, res)
            if classe.categoria == "ofensivo":
                ofensas = int((repo.obter_lead(lead.id).ficha or {}).get("ofensas") or 0) + 1
                repo.atualizar_ficha(lead.id, {"ofensas": ofensas})
                if ofensas >= 2:
                    return _passar_agressivo(lead.id, tipo_origem, cb, res)
            res.bloqueio_entrada = classe.categoria
            return _entregar(lead.id, prompts.fixa(classe.categoria), tipo_origem, cb, res)

        # Etapa 05
        texto_resp, tools = rodar_agente(lead.id, cb)
        res.tools_chamadas = tools
        res.transferido = "transferir_humano" in tools

        # Etapa 06
        veredito = guard_saida.verificar(texto_resp, lead.id, texto, cb)
        rastreio.evento("guardrail_saida", aprovado=veredito.aprovado, motivo=veredito.motivo)
        if not veredito.aprovado:
            res.bloqueios_saida.append(veredito.motivo)
            repo.salvar_mensagem(lead.id, "agente", texto_resp, tipo_origem, status="bloqueada",
                                 motivo_bloqueio=veredito.motivo)
            nova = guard_saida.reescrever(texto_resp, veredito.motivo, texto, cb)
            veredito2 = guard_saida.verificar(nova, lead.id, texto, cb)
            if veredito2.aprovado:
                texto_resp = nova
            else:
                res.bloqueios_saida.append(veredito2.motivo)
                repo.salvar_mensagem(lead.id, "agente", nova, tipo_origem, status="bloqueada",
                                     motivo_bloqueio=veredito2.motivo)
                texto_resp = prompts.fixa("segura")

        # Etapa 07
        return _entregar(lead.id, texto_resp, tipo_origem, cb, res)

    except llm.SemChaveOpenAI as e:
        res.ignorado = str(e)
        res.textos = [f"⚠️ {e}"]
        return res
    except Exception:
        log.exception("Erro no pipeline")
        res.ignorado = "erro"
        res.textos = [prompts.fixa("erro")]
        return res
