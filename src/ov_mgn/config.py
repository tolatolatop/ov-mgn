import os
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import BaseModel, Field, field_validator

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class AppConfig(BaseModel):
    app_name: str = Field(default="ov-mgn")
    docker_host: str | None = Field(default=None)
    data_root: Path = Field(default=Path("./data"))
    log_level: LogLevel = Field(default="INFO")

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.upper()


def load_config(env_file: str | Path = ".env") -> AppConfig:
    env_values = {}
    env_path = Path(env_file)
    if env_path.exists():
        env_values = {
            key: value for key, value in dotenv_values(env_path).items() if value is not None
        }

    def getenv(key: str, default: str | None = None) -> str | None:
        return os.getenv(key, env_values.get(key, default))

    return AppConfig(
        app_name=getenv("OV_MGN_APP_NAME", "ov-mgn"),
        docker_host=getenv("OV_MGN_DOCKER_HOST"),
        data_root=Path(getenv("OV_MGN_DATA_ROOT", "./data") or "./data"),
        log_level=getenv("OV_MGN_LOG_LEVEL", "INFO") or "INFO",
    )
