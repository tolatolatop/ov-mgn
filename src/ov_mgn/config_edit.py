import json
from typing import Any


class ConfigPathError(ValueError):
    pass


def parse_json_value(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def get_config_path(payload: dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in split_config_path(path):
        if not isinstance(current, dict) or part not in current:
            raise ConfigPathError(f"config path not found: {path}")
        current = current[part]
    return current


def set_config_path(payload: dict[str, Any], path: str, value: Any) -> None:
    parts = split_config_path(path)
    parent = get_config_parent(payload, parts, path)
    key = parts[-1]
    if key not in parent and not (is_openviking_map_path(parts) or is_optional_config_field(parts)):
        raise ConfigPathError(f"config path not found: {path}")
    parent[key] = value


def unset_config_path(payload: dict[str, Any], path: str) -> None:
    parts = split_config_path(path)
    parent = get_config_parent(payload, parts, path)
    key = parts[-1]
    if key not in parent:
        raise ConfigPathError(f"config path not found: {path}")
    if not is_openviking_map_path(parts) and not is_optional_config_field(parts):
        raise ConfigPathError(f"config path cannot be unset: {path}")
    del parent[key]


def get_config_parent(
    payload: dict[str, Any], parts: list[str], original_path: str
) -> dict[str, Any]:
    if len(parts) < 2:
        raise ConfigPathError(f"config path cannot be edited: {original_path}")
    current: Any = payload
    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            raise ConfigPathError(f"config path not found: {original_path}")
        current = current[part]
    if not isinstance(current, dict):
        raise ConfigPathError(f"config path is not editable: {original_path}")
    return current


def split_config_path(path: str) -> list[str]:
    parts = path.split(".")
    if any(not part for part in parts):
        raise ConfigPathError(f"invalid config path: {path}")
    return parts


def is_openviking_map_path(parts: list[str]) -> bool:
    return (
        len(parts) == 5
        and parts[0] == "services"
        and parts[2] == "openviking"
        and parts[3] in {"env", "vars"}
    )


def is_optional_config_field(parts: list[str]) -> bool:
    if len(parts) == 2 and parts[0] == "defaults" and parts[1] == "secret_env_file":
        return True
    if (
        len(parts) == 3
        and parts[0] == "defaults"
        and parts[1] == "openviking"
        and parts[2] == "root_api_key"
    ):
        return True
    if len(parts) == 3 and parts[0] == "services" and parts[2] == "image":
        return True
    if len(parts) == 3 and parts[0] == "services" and parts[2] == "route_path":
        return True
    if (
        len(parts) == 4
        and parts[0] == "services"
        and parts[2] == "source"
        and parts[3] in {"repo", "ref", "path"}
    ):
        return True
    return (
        len(parts) == 4
        and parts[0] == "services"
        and parts[2] == "openviking"
        and parts[3] == "template_path"
    )
