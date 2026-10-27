from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:////data/newsletter.sqlite"
    base_url: str = "http://localhost:8000"
    app_password_hash: str = ""
    session_secret: str = ""
    link_signing_secret: str = ""
    api_token: str = ""
    resend_api_key: str = ""
    email_from: str = ""
    email_to: str = ""
    embed_model: str = "intfloat/e5-base-v2"
    backup_dir: str = "/data/backups"


@lru_cache
def get_settings() -> Settings:
    return Settings()
