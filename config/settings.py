"""
config/settings.py — 환경설정 스키마.

운영에서 바뀌는 값들은 `.env` 에서 로드한다 (Pydantic Settings).
경로(BASE_DIR 등)는 repo 위치에서 자동 계산하므로 .env 와 무관.
"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Cloud API keys (선택 — 비워두면 클라우드 LLM 미사용) ─
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    GOOGLE_API_KEY: str = ""

    # ── LLM / Ollama (.env 필수) ──────────────────────────────
    OLLAMA_BASE_URL: str
    DEFAULT_PROVIDER: str
    DEFAULT_MODEL: str

    # ── App (.env 필수) ───────────────────────────────────────
    APP_PORT: int
    MAX_UPLOAD_SIZE_MB: int

    # ── 정적 (코드 상수 / 컴퓨티드 경로 — .env 무관) ──────────
    APP_TITLE: str = "KETI AI Platform"
    BASE_DIR: Path = _BASE_DIR
    UPLOAD_DIR: Path = _BASE_DIR / "uploads"
    RESULTS_DIR: Path = _BASE_DIR / "results"
    CHATS_DIR: Path = _BASE_DIR / "chats"


def _init_settings() -> Settings:
    s = Settings()
    s.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    s.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    s.CHATS_DIR.mkdir(parents=True, exist_ok=True)
    return s


settings = _init_settings()
