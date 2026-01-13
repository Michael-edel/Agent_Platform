import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

@dataclass
class Settings:
    openai_api_key: str
    onec_base_url: Optional[str] = None
    onec_token: Optional[str] = None
    telegram_bot_token: Optional[str] = None
    db_path: str = "inventory.db"
    openai_model: str = "gpt-4o-mini"
    max_workers: int = 3
    max_openai_concurrency: int = 2
    price_per_1k_in: float = 0.15  # Цена за 1K входных токенов в USD
    price_per_1k_out: float = 0.60  # Цена за 1K выходных токенов в USD
    openai_min_interval_sec: float = 0.7  # Минимальный интервал между запросами (троттлинг)
    openai_max_retries: int = 5  # Максимальное количество повторов при 429 ошибке

def load_settings(env_path: str = ".env") -> Settings:
    """Загрузка настроек из .env и переменных окружения."""
    load_dotenv(env_path)

    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        onec_base_url=os.getenv("ONEC_BASE_URL", "").strip() or None,
        onec_token=os.getenv("ONEC_TOKEN", "").strip() or None,
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip() or None,
        db_path=os.getenv("DB_PATH", "inventory.db").strip(),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip(),
        max_workers=int(os.getenv("MAX_WORKERS", "3")),
        max_openai_concurrency=int(os.getenv("MAX_OPENAI_CONCURRENCY", "2")),
        price_per_1k_in=float(os.getenv("PRICE_PER_1K_IN", "0.15")),
        price_per_1k_out=float(os.getenv("PRICE_PER_1K_OUT", "0.60")),
        openai_min_interval_sec=float(os.getenv("OPENAI_MIN_INTERVAL_SEC", "0.7")),
        openai_max_retries=int(os.getenv("OPENAI_MAX_RETRIES", "5")),
    )
