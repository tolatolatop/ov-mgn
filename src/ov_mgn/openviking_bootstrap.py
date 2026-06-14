import json

from ov_mgn.docker import DockerClient
from ov_mgn.logging import get_logger
from ov_mgn.server_config import LockedServiceSpec, configure_openviking_user

logger = get_logger(__name__)


def bootstrap_openviking_user(
    service_name: str,
    service: LockedServiceSpec,
    client: DockerClient,
) -> LockedServiceSpec:
    logger.debug(
        "OpenViking bootstrap check service=%s release_id=%s container=%s dry_run=%s",
        service_name,
        service.release_id,
        service.release_container_name,
        getattr(client, "dry_run", False),
    )
    if not openviking_root_api_key_present(service):
        logger.debug("OpenViking bootstrap skipped; root key unavailable service=%s", service_name)
        return service
    if getattr(client, "dry_run", False):
        logger.debug("OpenViking bootstrap skipped for dry-run service=%s", service_name)
        return service

    client.wait_healthy(service.release_container_name)
    logger.debug("OpenViking container healthy service=%s", service_name)
    client.settle(5)
    user_key = ensure_openviking_user_key(service, client)
    return configure_openviking_user(service, api_key=user_key)


def openviking_root_api_key_present(service: LockedServiceSpec) -> bool:
    if service.openviking.cli_config_file is None or not service.openviking.config_file.exists():
        return False
    try:
        payload = json.loads(service.openviking.config_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    root_api_key = (
        payload.get("server", {}).get("root_api_key") if isinstance(payload, dict) else None
    )
    return isinstance(root_api_key, str) and bool(root_api_key)


def ensure_openviking_user_key(service: LockedServiceSpec, client: DockerClient) -> str:
    logger.debug(
        "registering OpenViking default user container=%s",
        service.release_container_name,
    )
    register = client.exec(
        service.release_container_name,
        ["ov", "admin", "register-user", "default", "default", "-o", "json"],
        capture_output=True,
        allow_failure=True,
    )
    if register and register.returncode == 0:
        return extract_user_key(register.stdout)

    logger.debug(
        "regenerating OpenViking default user key container=%s",
        service.release_container_name,
    )
    regenerate = client.exec(
        service.release_container_name,
        ["ov", "admin", "regenerate-key", "default", "default", "-o", "json"],
        capture_output=True,
    )
    if regenerate is None:
        raise RuntimeError("failed to bootstrap OpenViking user key")
    return extract_user_key(regenerate.stdout)


def extract_user_key(output: str) -> str:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError("OpenViking user bootstrap did not return JSON") from exc
    result = payload.get("result") if isinstance(payload, dict) else None
    user_key = result.get("user_key") if isinstance(result, dict) else None
    if not isinstance(user_key, str) or not user_key:
        raise RuntimeError("OpenViking user bootstrap response did not include user_key")
    return user_key
