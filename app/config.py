import os
from typing import Optional

class Config:
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///data/app.db")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    WEBHOOK_SECRET: Optional[str] = os.getenv("WEBHOOK_SECRET")
    WEBHOOK_FORWARD_URL: Optional[str] = os.getenv("WEBHOOK_FORWARD_URL")
    ENABLE_ASYNC_WORKER: bool = os.getenv("ENABLE_ASYNC_WORKER", "true").lower() in ("true", "1", "yes")
    WORKER_MAX_RETRIES: int = int(os.getenv("WORKER_MAX_RETRIES", "3"))
    WORKER_BACKOFF_FACTOR: float = float(os.getenv("WORKER_BACKOFF_FACTOR", "2.0"))
    WORKER_POLL_INTERVAL: float = float(os.getenv("WORKER_POLL_INTERVAL", "1.0"))
    RATE_LIMIT_RPS: int = int(os.getenv("RATE_LIMIT_RPS", "100"))
    DB_POOL_MIN_SIZE: int = int(os.getenv("DB_POOL_MIN_SIZE", "10"))
    DB_POOL_MAX_SIZE: int = int(os.getenv("DB_POOL_MAX_SIZE", "20"))
    DB_POOL_TIMEOUT: float = float(os.getenv("DB_POOL_TIMEOUT", "10.0"))

    @classmethod
    def validate(cls) -> bool:
        return cls.WEBHOOK_SECRET is not None and cls.WEBHOOK_SECRET != ""

config = Config()
