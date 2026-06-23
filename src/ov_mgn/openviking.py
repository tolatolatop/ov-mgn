import json
import secrets
from pathlib import Path
from typing import Any

from ov_mgn.logging import get_logger
from ov_mgn.server_config import (
    OPENVIKING_MANAGED_ENV_KEYS,
    OPENVIKING_MODEL_SECTIONS,
    LockedServiceSpec,
    UserServerConfig,
)

logger = get_logger(__name__)


def validate_openviking_model_config(config: UserServerConfig) -> None:
    if not config.services:
        return
    deprecated = [
        name
        for name, service in sorted(config.services.items())
        if service.openviking.template_path is not None
    ]
    if deprecated:
        services = ", ".join(deprecated)
        raise ValueError(
            "services."
            f"{services}.openviking.template_path is deprecated; remove it and use "
            "defaults.openviking.model_config_file"
        )
    logger.debug(
        "validating OpenViking model config file=%s services=%d",
        config.defaults.openviking.model_config_file.expanduser(),
        len(config.services),
    )
    _load_model_config(config.defaults.openviking.model_config_file)


def render_managed_openviking_config(service: LockedServiceSpec) -> None:
    render_managed_openviking_config_with_key(service, root_api_key=None)


def render_managed_openviking_config_with_key(
    service: LockedServiceSpec,
    *,
    root_api_key: str | None,
) -> None:
    if service.openviking.template_path is not None:
        raise ValueError(
            "openviking.template_path is deprecated; remove it and use "
            "defaults.openviking.model_config_file"
        )
    existing_root_key = _existing_root_api_key(service.openviking.config_file)
    resolved_root_api_key = root_api_key or existing_root_key or secrets.token_urlsafe(32)
    logger.debug(
        "rendering OpenViking config service_release=%s config_dir=%s model_config_file=%s "
        "shared_root_key=%s reused_root_key=%s",
        service.release_id,
        service.config_dir,
        service.openviking.model_config_file,
        bool(root_api_key),
        bool(existing_root_key),
    )
    model_config = _load_model_config(service.openviking.model_config_file)
    config: dict[str, Any] = {
        "server": {
            "host": "0.0.0.0",
            "port": service.backend_port,
            "root_api_key": resolved_root_api_key,
        },
        "storage": {
            "workspace": "/app/data",
            "vectordb": {"name": "context", "backend": "local"},
            "agfs": {"backend": "local"},
        },
    }
    config.update(model_config)
    rendered = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
    service.openviking.config_file.parent.mkdir(parents=True, exist_ok=True)
    service.openviking.config_file.write_text(rendered, encoding="utf-8")
    render_openviking_cli_config(service, rendered)


def configure_openviking_user(
    service: LockedServiceSpec,
    *,
    api_key: str,
    account: str = "default",
    user: str = "default",
) -> LockedServiceSpec:
    cli_config_file = (
        service.openviking.cli_config_file or service.openviking.config_file.with_name("ovcli.conf")
    )
    cli_config = {
        "url": f"http://127.0.0.1:{service.backend_port}",
        "api_key": api_key,
        "account": account,
        "user": user,
    }
    logger.debug(
        "writing OpenViking runtime user config service_release=%s cli_config_file=%s",
        service.release_id,
        cli_config_file,
    )
    cli_config_file.parent.mkdir(parents=True, exist_ok=True)
    cli_config_file.write_text(
        json.dumps(cli_config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    cli_config_file.chmod(0o600)

    runtime_env_file = service.config_dir / "runtime.env"
    runtime_env_text = _merged_runtime_env(
        service.secret_env_file,
        {
            "VIKINGBOT_ENDPOINT": f"http://127.0.0.1:{service.backend_port}/bot/v1",
            "VIKINGBOT_API_KEY": api_key,
        },
    )
    runtime_env_file.write_text(runtime_env_text, encoding="utf-8")
    runtime_env_file.chmod(0o600)
    return service.model_copy(update={"secret_env_file": runtime_env_file})


def render_openviking_cli_config(
    service: LockedServiceSpec,
    openviking_config: str,
) -> None:
    try:
        parsed = json.loads(openviking_config)
    except json.JSONDecodeError:
        return
    root_api_key = parsed.get("server", {}).get("root_api_key")
    if not root_api_key:
        return
    cli_config = {
        "url": f"http://127.0.0.1:{service.backend_port}",
        "api_key": root_api_key,
        "account": "default",
        "user": "default",
    }
    cli_config_file = (
        service.openviking.cli_config_file or service.openviking.config_file.with_name("ovcli.conf")
    )
    logger.debug(
        "writing OpenViking root cli config service_release=%s cli_config_file=%s",
        service.release_id,
        cli_config_file,
    )
    cli_config_file.write_text(
        json.dumps(cli_config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def render_openviking_cli_wrapper(service: LockedServiceSpec) -> None:
    wrapper_dir = service.config_dir / "bin"
    wrapper_dir.mkdir(parents=True, exist_ok=True)
    wrapper = wrapper_dir / "ov"
    logger.debug(
        "writing OpenViking wrapper service_release=%s wrapper=%s",
        service.release_id,
        wrapper,
    )
    wrapper.write_text(
        f"""#!/bin/sh
set -eu
if [ "${{1:-}}" = "admin" ]; then
  exec /app/.venv/bin/ov "$@"
fi

api_key="$(python - <<'PY' 2>/dev/null || true
import json
try:
    config = json.load(open('/app/config/openviking.conf'))
    print(config.get('server', {{}}).get('root_api_key', ''))
except Exception:
    pass
PY
)"
if [ -n "$api_key" ]; then
  export VIKINGBOT_API_KEY="${{VIKINGBOT_API_KEY:-$api_key}}"
fi
export VIKINGBOT_ENDPOINT="${{VIKINGBOT_ENDPOINT:-http://127.0.0.1:{service.backend_port}/bot/v1}}"
exec /app/.venv/bin/ov "$@"
""",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)


def _validate_dense_dimension(payload: dict[str, Any]) -> None:
    """Validate embedding.dense.dimension if present (advanced config)."""
    embedding = payload.get("embedding")
    if not isinstance(embedding, dict):
        return
    dense = embedding.get("dense")
    if not isinstance(dense, dict):
        return
    if "dimension" not in dense:
        return
    dim = dense["dimension"]
    if not isinstance(dim, int) or isinstance(dim, bool) or dim <= 0:
        raise ValueError(f"embedding.dense.dimension must be a positive integer; got {dim!r}")


def _load_model_config(path: Path) -> dict[str, Any]:
    model_config_path = path.expanduser()
    if not model_config_path.exists() or not model_config_path.is_file():
        raise ValueError(f"model_config_file must exist and be a file: {model_config_path}")
    try:
        payload = json.loads(model_config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"model_config_file must be valid JSON: {model_config_path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("model_config_file must contain a JSON object")
    unknown = sorted(set(payload) - OPENVIKING_MODEL_SECTIONS)
    if unknown:
        raise ValueError(
            "model_config_file may only contain top-level sections: "
            f"{', '.join(sorted(OPENVIKING_MODEL_SECTIONS))}; found {', '.join(unknown)}"
        )
    if not any(section in payload for section in OPENVIKING_MODEL_SECTIONS):
        raise ValueError("model_config_file must contain at least one of embedding, vlm")
    _validate_dense_dimension(payload)
    return payload


def _existing_root_api_key(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    root_api_key = (
        payload.get("server", {}).get("root_api_key") if isinstance(payload, dict) else None
    )
    return root_api_key if isinstance(root_api_key, str) and root_api_key else None


def _merged_runtime_env(secret_env_file: Path | None, values: dict[str, str]) -> str:
    lines: list[str] = []
    if secret_env_file:
        secret_path = secret_env_file.expanduser()
        if secret_path.exists():
            lines.extend(
                line
                for line in secret_path.read_text(encoding="utf-8").splitlines()
                if _env_line_key(line) not in OPENVIKING_MANAGED_ENV_KEYS
            )
            lines.append("")
    lines.extend(f"{key}={value}" for key, value in sorted(values.items()))
    return "\n".join(lines).rstrip() + "\n"


def _env_line_key(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    return stripped.split("=", 1)[0].strip()
