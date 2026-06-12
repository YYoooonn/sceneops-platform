from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DbSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=None,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    sceneops_database_url: str = Field(alias="SCENEOPS_DATABASE_URL")


@lru_cache
def get_db_settings() -> DbSettings:
    return DbSettings()
