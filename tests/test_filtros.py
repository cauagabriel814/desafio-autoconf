from app.canais import evolution
from app.canais.payload import MensagemEntrada
from app.db import repo
from app.pipeline import filtros, orquestrador


def _msg(texto, canal="whatsapp", tipo="texto", cliente="5511999990001", mid=None):
    import uuid

    return MensagemEntrada(canal=canal, cliente_id=cliente, message_id=mid or uuid.uuid4().hex, tipo=tipo,
                           conteudo=texto)


def test_gatilho_e_removido():
    assert filtros.contem_gatilho("#THREAD quero um suv")
    assert filtros.remover_gatilho("#thread quero um suv") == "quero um suv"


def test_whatsapp_sem_gatilho_e_ignorado():
    assert orquestrador.aceitar(_msg("quero um suv", cliente="5511000000001")) == (False, "sem_gatilho")


def test_whatsapp_com_gatilho_passa_e_abre_sessao():
    assert orquestrador.aceitar(_msg("#thread quero um suv", cliente="5511000000002")) == (True, None)
    # áudio logo depois passa sem palavra-chave
    assert orquestrador.aceitar(_msg("", tipo="audio", cliente="5511000000002")) == (True, None)


def test_audio_sem_sessao_e_ignorado():
    assert orquestrador.aceitar(_msg("", tipo="audio", cliente="5511000000003")) == (False, "sem_gatilho")


def test_web_nao_precisa_de_gatilho():
    assert orquestrador.aceitar(_msg("quero um suv", canal="web", cliente="web-1")) == (True, None)


def test_duplicada():
    m = _msg("#thread oi", cliente="5511000000004", mid="dup-1")
    assert orquestrador.aceitar(m) == (True, None)
    assert orquestrador.aceitar(m) == (False, "duplicada")


def test_com_humano_so_grava():
    lead = repo.obter_ou_criar_lead("web", "web-humano")
    repo.definir_status(lead.id, "humano")
    assert orquestrador.aceitar(_msg("oi?", canal="web", cliente="web-humano")) == (False, "com_humano")
    msgs = repo.detalhe_lead(lead.id)["mensagens"]
    assert msgs[-1]["status"] == "ignorada"


def test_converter_webhook_texto():
    body = {
        "event": "messages.upsert",
        "data": {
            "key": {"remoteJid": "5511988887777@s.whatsapp.net", "fromMe": False, "id": "ABC"},
            "pushName": "João",
            "message": {"conversation": "#thread oi"},
        },
    }
    m = evolution.converter(body)
    assert m.cliente_id == "5511988887777" and m.tipo == "texto" and m.nome == "João"


def test_converter_ignora_grupo_e_fromme():
    grupo = {"event": "messages.upsert", "data": {"key": {"remoteJid": "123@g.us", "id": "1"},
                                                  "message": {"conversation": "oi"}}}
    eu = {"event": "messages.upsert", "data": {"key": {"remoteJid": "55@s.whatsapp.net", "fromMe": True,
                                                       "id": "2"}, "message": {"conversation": "oi"}}}
    assert evolution.converter(grupo) is None
    assert evolution.converter(eu) is None


def test_converter_audio():
    body = {"event": "messages.upsert", "data": {
        "key": {"remoteJid": "5511@s.whatsapp.net", "id": "3"},
        "message": {"audioMessage": {"mimetype": "audio/ogg; codecs=opus"}, "base64": "AAAA"}}}
    m = evolution.converter(body)
    assert m.tipo == "audio" and m.midia_b64 == "AAAA"
