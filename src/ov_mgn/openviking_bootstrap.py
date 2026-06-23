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
    root_key = read_openviking_root_api_key(service)
    if root_key is None:
        logger.debug("OpenViking root key unavailable after startup service=%s", service_name)
        return service
    return configure_openviking_user(service, api_key=root_key)


def openviking_root_api_key_present(service: LockedServiceSpec) -> bool:
    return read_openviking_root_api_key(service) is not None


def read_openviking_root_api_key(service: LockedServiceSpec) -> str | None:
    if service.openviking.cli_config_file is None or not service.openviking.config_file.exists():
        return None
    try:
        payload = json.loads(service.openviking.config_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    root_api_key = (
        payload.get("server", {}).get("root_api_key") if isinstance(payload, dict) else None
    )
    return root_api_key if isinstance(root_api_key, str) and bool(root_api_key) else None
