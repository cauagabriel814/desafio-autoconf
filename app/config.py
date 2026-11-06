from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_stt_model: str = "gpt-4o-mini-transcribe"
    openai_tts_model: str = "gpt-4o-mini-tts"
    openai_tts_voice: str = "coral"

    # Evolution API
    evolution_api_url: str = "http://localhost:8080"
    evolution_api_key: str = ""
    evolution_instance: str = "autoconf"

    # Gatilho: só vale para o WhatsApp
    trigger_keyword: str = "#thread"
    # Com um número só: atende as mensagens que você manda para você mesmo ("Mensagem para você"). Desligado, o bot
    # ignora tudo o que sai do próprio número.
    aceitar_proprio_numero: str = "off"
    # Comandos de teste: reinicia a conversa / faz o agente parar de responder.
    # Só valem com TEST=on e nunca na tela de chat.
    test: str = "off"
    reset_keyword: str = "#reset"
    stop_keyword: str = "#parar"
    # Depois de um #thread, áudios e imagens sem legenda passam por este tempo
    sessao_minutos: int = 30

    # Langfuse (opcional)
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # Dados
    database_url: str = "sqlite:///data/autoconf.db"
    data_dir: str = "data"

    # Comportamento
    # Quando responder em áudio: espelhar (áudio→áudio, texto→texto) | sempre_texto | sempre_audio | texto_e_audio
    modo_resposta: str = "espelhar"
    agrupar_segundos: float = 5.0
    historico_n: int = 20
    nome_atendente: str = "Lia"
    nome_loja: str = "Autoconf Veículos"

    @property
    def audios_dir(self) -> Path:
        p = Path(self.data_dir) / "audios"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def proprio_numero_ativo(self) -> bool:
        return self.aceitar_proprio_numero.strip().lower() in ("on", "true", "1", "sim")

    @property
    def comandos_ativos(self) -> bool:
        return self.test.strip().lower() in ("on", "true", "1", "sim")

    @property
    def langfuse_ativo(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
