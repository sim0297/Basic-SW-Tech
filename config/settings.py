from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    GOOGLE_API_KEY: str = ""
    OLLAMA_BASE_URL: str = "http://localhost:11434"

    DEFAULT_PROVIDER: str = "ollama"
    DEFAULT_MODEL: str = "qwen3:8b"

    BASE_DIR: Path = Path(__file__).resolve().parent.parent
    UPLOAD_DIR: Path = BASE_DIR / "uploads"
    RESULTS_DIR: Path = BASE_DIR / "results"

    MAX_UPLOAD_SIZE_MB: int = 100
    APP_TITLE: str = "KETI AI Platform"
    APP_PORT: int = 8705


def _init_settings() -> Settings:
    s = Settings()
    s.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    s.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    return s


settings = _init_settings()
