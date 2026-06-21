import json
import os
import tempfile
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from ov_mgn.server_config import (
    OPENVIKING_MODEL_SECTIONS,
    ServerDefaults,
    ServiceSpec,
    UserServerConfig,
    get_server_config_path,
    save_user_server_config,
    save_user_server_config_data,
)

API_KEY_PLACEHOLDER = "replace-me"
MODEL_CONFIG_NAME = "model.json"


class WizardPrompts(Protocol):
    def input(self, message: str, *, default: str = "") -> str: ...

    def path(
        self,
        message: str,
        *,
        default: str = "",
        only_directories: bool = False,
        only_files: bool = False,
        mandatory: bool = True,
    ) -> str: ...

    def secret(self, message: str, *, default: str = "") -> str: ...

    def confirm(self, message: str, *, default: bool = False) -> bool: ...

    def select(self, message: str, choices: list[str], *, default: str | None = None) -> str: ...

    def select_key(
        self,
        message: str,
        choices: list[tuple[str, str]],
        *,
        default: str | None = None,
    ) -> str: ...


class InquirerPrompts:
    def input(self, message: str, *, default: str = "") -> str:
        from InquirerPy import inquirer

        return str(inquirer.text(message=message, default=default).execute())

    def path(
        self,
        message: str,
        *,
        default: str = "",
        only_directories: bool = False,
        only_files: bool = False,
        mandatory: bool = True,
    ) -> str:
        from InquirerPy import inquirer

        return str(
            inquirer.filepath(
                message=message,
                default=default,
                instruction="(Tab 补全)",
                only_directories=only_directories,
                only_files=only_files,
                mandatory=mandatory,
            ).execute()
        )

    def secret(self, message: str, *, default: str = "") -> str:
        from InquirerPy import inquirer

        return str(inquirer.secret(message=message, default=default).execute())

    def confirm(self, message: str, *, default: bool = False) -> bool:
        from InquirerPy import inquirer

        return bool(inquirer.confirm(message=message, default=default).execute())

    def select(self, message: str, choices: list[str], *, default: str | None = None) -> str:
        from InquirerPy import inquirer

        return str(inquirer.select(message=message, choices=choices, default=default).execute())

    def select_key(
        self,
        message: str,
        choices: list[tuple[str, str]],
        *,
        default: str | None = None,
    ) -> str:
        from InquirerPy import inquirer
        from InquirerPy.base.control import Choice

        return str(
            inquirer.select(
                message=message,
                choices=[Choice(value=value, name=label) for value, label in choices],
                default=default,
            ).execute()
        )


def get_model_config_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".ov_mgn" / MODEL_CONFIG_NAME


def run_init_wizard(
    prompts: WizardPrompts,
    *,
    config_path: Path | None = None,
    model_path: Path | None = None,
) -> list[Path]:
    server_path = config_path or get_server_config_path()
    resolved_model_path = model_path or get_model_config_path()
    written: list[Path] = []
    server_config: UserServerConfig | None = None
    model_payload: dict[str, Any] | None = None

    server_action = _server_init_action(prompts, server_path)
    if server_action == "create":
        config = UserServerConfig(
            defaults={"openviking": {"model_config_file": resolved_model_path}},
            services={},
        )
        if prompts.confirm("现在配置第一个服务？", default=True):
            config = add_service_to_config(config, prompt_service(prompts))
        server_config = config
    elif server_action == "add-service":
        config = _load_server_config_or_empty(server_path)
        server_config = add_service_to_config(config, prompt_service(prompts))

    model_action = _model_init_action(prompts, resolved_model_path)
    if model_action == "write":
        model_payload = prompt_model_config(prompts)
        validate_model_config_payload(model_payload)

    if server_config is not None:
        if _confirm_write(prompts, server_path):
            written.append(save_user_server_config(server_config, server_path))
    if model_payload is not None:
        if _confirm_write(prompts, resolved_model_path):
            written.append(write_model_config(model_payload, resolved_model_path))

    return written


def run_add_service_wizard(
    prompts: WizardPrompts,
    *,
    config_path: Path | None = None,
) -> Path | None:
    server_path = config_path or get_server_config_path()
    config = _load_server_config_or_empty(server_path)
    updated = add_service_to_config(config, prompt_service(prompts))
    if not _confirm_write(prompts, server_path):
        return None
    return save_user_server_config(updated, server_path)


def run_edit_wizard(
    prompts: WizardPrompts,
    *,
    config_path: Path | None = None,
    model_path: Path | None = None,
) -> list[Path]:
    server_path = config_path or get_server_config_path()
    resolved_model_path = model_path or get_model_config_path()
    server_payload: dict[str, Any] | None = None
    model_payload: dict[str, Any] | None = None

    while True:
        target = prompts.select_key(
            "要编辑哪类配置？",
            [("server", "服务配置"), ("model", "模型配置")],
            default="server",
        )
        if target == "server":
            current_server = server_payload or _load_server_payload(server_path)
            server_payload = edit_server_config(prompts, current_server)
        else:
            current_model = model_payload or _load_model_payload(resolved_model_path)
            model_payload = edit_model_config(prompts, current_model)
        if not prompts.confirm("保存前继续修改其他配置？", default=False):
            break

    written: list[Path] = []
    if server_payload is not None and _confirm_write(prompts, server_path):
        written.append(save_user_server_config_data(server_payload, server_path))
    if model_payload is not None and _confirm_write(prompts, resolved_model_path):
        written.append(write_model_config(model_payload, resolved_model_path))
    return written


def edit_model_config(prompts: WizardPrompts, payload: dict[str, Any]) -> dict[str, Any]:
    section = prompts.select_key(
        "模型能力",
        [
            ("embedding", "Embedding 检索模型"),
            ("vlm", "VLM 视觉/语言模型"),
        ],
        default="embedding",
    )
    payload[section] = _prompt_model_section(prompts, section, payload.get(section))
    validate_model_config_payload(payload)
    return payload


def edit_server_config(prompts: WizardPrompts, payload: dict[str, Any]) -> dict[str, Any]:
    section = prompts.select_key(
        "服务配置范围",
        [("defaults", "默认配置"), ("service", "具体服务")],
        default="service",
    )
    if section == "defaults":
        return edit_server_defaults(prompts, payload)
    return edit_server_service(prompts, payload)


def edit_server_defaults(prompts: WizardPrompts, payload: dict[str, Any]) -> dict[str, Any]:
    config = UserServerConfig.model_validate(payload)
    defaults = prompt_server_defaults(prompts, config.defaults)
    updated = UserServerConfig.model_validate(config.model_copy(update={"defaults": defaults}))
    return updated.model_dump(mode="json", exclude_none=True)


def prompt_server_defaults(prompts: WizardPrompts, defaults: ServerDefaults) -> ServerDefaults:
    image = prompts.input("默认 OpenViking 镜像", default=defaults.image).strip()
    data_root = prompts.path("数据目录", default=str(defaults.data_root)).strip()
    secret_env_file = prompts.path(
        "Docker secret env 文件（留空跳过）",
        default=str(defaults.secret_env_file) if defaults.secret_env_file else "",
        only_files=True,
        mandatory=False,
    ).strip()
    values: dict[str, Any] = {
        "image": image,
        "backend_port": defaults.backend_port,
        "data_root": data_root,
        "secret_env_file": secret_env_file or None,
        "openviking": {
            "model_config_file": defaults.openviking.model_config_file,
        },
        "gateway": defaults.gateway.model_dump(mode="json"),
        "port_range": defaults.port_range,
    }

    if prompts.confirm("配置高级默认选项？", default=False):
        model_config_file = prompts.path(
            "模型配置文件",
            default=str(defaults.openviking.model_config_file),
            only_files=True,
        ).strip()
        backend_port = prompts.input("容器内后端端口", default=str(defaults.backend_port)).strip()
        gateway_host = prompts.input("网关绑定地址", default=defaults.gateway.host).strip()
        gateway_port = prompts.input("网关绑定端口", default=str(defaults.gateway.port)).strip()
        gateway_image = prompts.input("网关镜像", default=defaults.gateway.image).strip()
        network_name = prompts.input(
            "网关 Docker 网络名", default=defaults.gateway.network_name
        ).strip()
        port_start = prompts.input(
            "候选服务端口范围起点", default=str(defaults.port_range[0])
        ).strip()
        port_end = prompts.input(
            "候选服务端口范围终点", default=str(defaults.port_range[1])
        ).strip()
        values.update(
            {
                "backend_port": int(backend_port),
                "openviking": {"model_config_file": model_config_file},
                "gateway": {
                    "enabled": True,
                    "host": gateway_host,
                    "port": int(gateway_port),
                    "image": gateway_image,
                    "network_name": network_name,
                },
                "port_range": [int(port_start), int(port_end)],
            }
        )

    return ServerDefaults.model_validate(values)


def edit_server_service(prompts: WizardPrompts, payload: dict[str, Any]) -> dict[str, Any]:
    config = UserServerConfig.model_validate(payload)
    if not config.services:
        raise ValueError("server.json has no services to edit")
    service_names = sorted(config.services)
    current_name = prompts.select("选择服务", service_names, default=service_names[0])
    current_service = config.services[current_name]
    service_data = prompt_service(prompts, name=current_name, service=current_service)
    new_name = service_data.pop("name")
    services = dict(config.services)
    if new_name != current_name and new_name in services:
        raise ValueError(f"service already exists: {new_name}")
    service_data["branch"] = current_service.branch
    services.pop(current_name)
    services[new_name] = ServiceSpec.model_validate(service_data)
    updated = UserServerConfig.model_validate(config.model_copy(update={"services": services}))
    return updated.model_dump(mode="json", exclude_none=True)


def add_service_to_config(
    config: UserServerConfig, service_data: dict[str, Any]
) -> UserServerConfig:
    name = service_data.pop("name")
    if name in config.services:
        raise ValueError(f"service already exists: {name}")
    services = dict(config.services)
    services[name] = ServiceSpec.model_validate(service_data)
    return UserServerConfig.model_validate(config.model_copy(update={"services": services}))


def prompt_service(
    prompts: WizardPrompts,
    *,
    name: str = "",
    service: ServiceSpec | None = None,
) -> dict[str, Any]:
    default_name = name
    existing_service = service
    source_spec = existing_service.source if existing_service else None
    openviking = existing_service.openviking if existing_service else None
    name = prompts.input("服务名称", default=default_name).strip()
    source_type = prompts.select_key(
        "源码类型",
        [("local", "本地目录"), ("git", "Git 仓库")],
        default=source_spec.type if source_spec else "local",
    )
    if source_type == "local":
        source: dict[str, Any] = {
            "type": "local",
            "path": prompts.path(
                "本地源码目录",
                default=str(source_spec.path) if source_spec and source_spec.path else ".",
                only_directories=True,
            ).strip(),
        }
    else:
        repo = prompts.input(
            "Git 仓库地址",
            default=source_spec.repo if source_spec and source_spec.repo else "",
        ).strip()
        source = {"type": "git", "repo": repo}

    openviking_data: dict[str, Any] = {
        "vars": dict(openviking.vars) if openviking else {},
        "env": dict(openviking.env) if openviking else {},
    }
    service_data: dict[str, Any] = {
        "name": name,
        "source": source,
        "openviking": openviking_data,
    }
    if existing_service and existing_service.route_path:
        service_data["route_path"] = existing_service.route_path
    if existing_service and existing_service.image:
        service_data["image"] = existing_service.image
    if source_type == "git" and source_spec and source_spec.ref:
        service_data["source"]["ref"] = source_spec.ref

    if prompts.confirm("配置高级服务选项？", default=False):
        if source_type == "git":
            ref = prompts.input(
                "Git ref（留空使用 HEAD）",
                default=source_spec.ref if source_spec and source_spec.ref else "",
            ).strip()
            if ref:
                service_data["source"]["ref"] = ref
            else:
                service_data["source"].pop("ref", None)
        route_path = prompts.input(
            "服务路由路径",
            default=service_data.get("route_path", f"/{name}/"),
        ).strip()
        image = prompts.input(
            "服务镜像（留空使用默认镜像）",
            default=service_data.get("image", ""),
        ).strip()
        if route_path:
            service_data["route_path"] = route_path
        else:
            service_data.pop("route_path", None)
        if image:
            service_data["image"] = image
        else:
            service_data.pop("image", None)
    return service_data


def prompt_model_config(prompts: WizardPrompts) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for section in ("embedding", "vlm"):
        if prompts.confirm(
            _model_section_confirm_message(section), default=(section == "embedding")
        ):
            payload[section] = _prompt_model_section(prompts, section)
    if not payload:
        raise ValueError("model.json must contain at least one of embedding, vlm")
    return payload


def _model_section_confirm_message(section: str) -> str:
    if section == "embedding":
        return "配置用于检索/搜索的 Embedding 模型？"
    if section == "vlm":
        return "配置视觉/语言模型？"
    return ""


def validate_model_config_payload(payload: dict[str, Any]) -> None:
    unknown = sorted(set(payload) - OPENVIKING_MODEL_SECTIONS)
    if unknown:
        raise ValueError(
            "model.json may only contain top-level sections: "
            f"{', '.join(sorted(OPENVIKING_MODEL_SECTIONS))}; found {', '.join(unknown)}"
        )
    if not any(section in payload for section in OPENVIKING_MODEL_SECTIONS):
        raise ValueError("model.json must contain at least one of embedding, vlm")


def write_model_config(payload: dict[str, Any], path: Path) -> Path:
    validate_model_config_payload(payload)
    return _write_json_atomic(payload, path)


def _prompt_model_section(
    prompts: WizardPrompts,
    section: str,
    current: Any = None,
) -> dict[str, Any]:
    defaults = _model_section_prompt_defaults(section, current)
    api_base = prompts.input(
        f"{_model_section_label(section)} API URL", default=defaults["api_base"]
    ).strip()
    api_key = prompts.secret(
        f"{_model_section_label(section)} API 密钥（留空写入 replace-me）",
        default=defaults["api_key"],
    ).strip()
    model = prompts.input(
        f"{_model_section_label(section)} 模型名", default=defaults["model"]
    ).strip()
    key = api_key or API_KEY_PLACEHOLDER

    if prompts.confirm("配置高级模型参数？（超时、并发、最大 token）", default=False):
        advanced = _prompt_advanced_model_options(prompts, section, current)
    else:
        advanced = {}

    if section == "embedding":
        dense: dict[str, Any] = {"provider": defaults["provider"], "api_key": key}
        if api_base:
            dense["api_base"] = api_base
        if model:
            dense["model"] = model
        result: dict[str, Any] = {"dense": dense}
        if advanced:
            result.update(advanced)
        return result

    data = {"provider": defaults["provider"], "api_key": key}
    if api_base:
        data["api_base"] = api_base
    if model:
        data["model"] = model
    if advanced:
        data.update(advanced)
    return data



def _prompt_advanced_model_options(
    prompts: WizardPrompts,
    section: str,
    current: Any = None,
) -> dict[str, Any]:
    """Prompt for advanced model options: timeout, max_tokens, max_concurrent.

    Returns a dict of advanced fields (empty if user skips all), using OpenViking
    defaults as fallback when no existing config value is available.
    """
    current = current if isinstance(current, dict) else {}
    advanced: dict[str, Any] = {}

    if section == "embedding":
        current_value = _get_nested_int(current, raw_key="max_concurrent")
        default_concurrent = str(current_value) if current_value is not None else "10"
        raw = prompts.input(
            "Embedding 最大并发请求数",
            default=default_concurrent,
        ).strip()
        if raw:
            advanced["max_concurrent"] = int(raw)

    else:
        # VLM / Bot
        current_timeout = _get_nested_int(current, raw_key="timeout")
        default_timeout = str(current_timeout) if current_timeout is not None else "60.0"
        raw = prompts.input(
            f"{_model_section_label(section)} 请求超时（秒）",
            default=default_timeout,
        ).strip()
        if raw:
            advanced["timeout"] = float(raw)

        current_max_tokens = _get_nested_int(current, raw_key="max_tokens")
        default_max_tokens = str(current_max_tokens) if current_max_tokens is not None else ""
        raw = prompts.input(
            f"{_model_section_label(section)} 最大输出 token（留空=provider 默认）",
            default=default_max_tokens,
        ).strip()
        if raw:
            advanced["max_tokens"] = int(raw)

        current_concurrent = _get_nested_int(current, raw_key="max_concurrent")
        default_concurrent = str(current_concurrent) if current_concurrent is not None else "100"
        raw = prompts.input(
            f"{_model_section_label(section)} 最大并发请求数",
            default=default_concurrent,
        ).strip()
        if raw:
            advanced["max_concurrent"] = int(raw)

    return advanced


def _get_nested_int(data: dict[str, Any], raw_key: str) -> int | None:
    """Extract an int value from a possibly-nested dict.

    For embedding section, current could be e.g. {"dense": {...}, "max_concurrent": 10}.
    This checks both top-level and nested keys.
    """
    if raw_key in data:
        val = data[raw_key]
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return int(val)
    for subkey in ("dense",):
        sub = data.get(subkey)
        if isinstance(sub, dict) and raw_key in sub:
            val = sub[raw_key]
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                return int(val)
    return None


def _model_section_prompt_defaults(section: str, current: Any) -> dict[str, str]:
    values = _model_section_values(section, current)
    return {
        "provider": values.get("provider", "openai"),
        "api_base": values.get("api_base", ""),
        "api_key": values.get("api_key", ""),
        "model": values.get("model", _default_model_name(section)),
    }


def _model_section_values(section: str, current: Any) -> dict[str, str]:
    if not isinstance(current, dict):
        return {}
    if section == "embedding":
        candidate = current.get("dense", {})
    else:
        candidate = current
    if not isinstance(candidate, dict):
        return {}
    values: dict[str, str] = {}
    for key in ("provider", "api_key", "model"):
        value = candidate.get(key)
        if isinstance(value, str):
            values[key] = value
    api_base = candidate.get("api_base", candidate.get("base_url", ""))
    if isinstance(api_base, str):
        values["api_base"] = api_base
    return values


def _default_model_name(section: str) -> str:
    if section == "embedding":
        return "text-embedding-3-small"
    return "gpt-4o-mini"


def _model_section_label(section: str) -> str:
    if section == "embedding":
        return "Embedding"
    if section == "vlm":
        return "VLM"
    return ""


def _server_init_action(prompts: WizardPrompts, server_path: Path) -> str:
    if not server_path.exists():
        return "create"
    return prompts.select_key(
        f"{server_path} 已存在",
        [("skip", "跳过"), ("add-service", "追加服务")],
        default="skip",
    )


def _model_init_action(prompts: WizardPrompts, model_path: Path) -> str:
    if not model_path.exists():
        return "write"
    action = prompts.select_key(
        f"{model_path} 已存在",
        [("skip", "跳过"), ("rewrite", "重写")],
        default="skip",
    )
    if action != "rewrite":
        return "skip"
    if not prompts.confirm("确认重写 model.json？", default=False):
        return "skip"
    return "write"


def _load_server_config_or_empty(path: Path) -> UserServerConfig:
    if not path.exists():
        return UserServerConfig()
    try:
        with path.open("r", encoding="utf-8") as file:
            return UserServerConfig.model_validate(json.load(file))
    except (OSError, json.JSONDecodeError, ValidationError, ValueError):
        raise


def _load_server_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    config = UserServerConfig.model_validate(payload)
    return config.model_dump(mode="json", exclude_none=True)


def _load_model_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError("model.json must contain a JSON object")
    validate_model_config_payload(payload)
    return payload


def _confirm_write(prompts: WizardPrompts, path: Path) -> bool:
    return prompts.confirm(f"确认写入 {path}？", default=False)


def _write_json_atomic(data: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2, sort_keys=True)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        temp_path.chmod(0o600)
        os.replace(temp_path, path)
        path.chmod(0o600)
    except Exception:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise
    return path
