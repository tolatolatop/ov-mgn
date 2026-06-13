import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
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
    env_path = Path(env_file)
    if env_path.exists():
        load_dotenv(env_path)

    return AppConfig(
        app_name=os.getenv("OV_MGN_APP_NAME", "ov-mgn"),
        docker_host=os.getenv("OV_MGN_DOCKER_HOST"),
        data_root=Path(os.getenv("OV_MGN_DATA_ROOT", "./data")),
        log_level=os.getenv("OV_MGN_LOG_LEVEL", "INFO"),
    )
