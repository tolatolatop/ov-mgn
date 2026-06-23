from datetime import UTC, datetime
from pathlib import Path

from ov_mgn.branch_data import prepare_release_data
from ov_mgn.docker import DockerClient, write_gateway_config
from ov_mgn.logging import get_logger
from ov_mgn.openviking_bootstrap import bootstrap_openviking_user
from ov_mgn.server_config import (
    LockedServerConfig,
    LockedServiceSpec,
    ReleaseLock,
    RuntimeServiceState,
    configure_openviking_user,
    load_locked_config,
    load_release_lock,
    load_state,
    load_user_server_config,
    materialize_service,
    write_release_lock,
    write_state,
)

logger = get_logger(__name__)


def up_service(
    service_name: str,
    *,
    lock_path: Path | None = None,
    release_path: Path | None = None,
    state_path: Path | None = None,
    docker: DockerClient | None = None,
) -> tuple[str, int]:
    logger.debug(
        "lifecycle up start service=%s lock_path=%s release_path=%s state_path=%s",
        service_name,
        lock_path,
        release_path,
        state_path,
    )
    locked = load_locked_config(lock_path)
    service = _get_locked_service(locked, service_name)
    if not service.enabled:
        raise ValueError(f"service {service_name} is disabled")

    root_api_key = _load_configured_root_api_key(locked)
    materialize_service(service, root_api_key=root_api_key)
    if root_api_key:
        service = configure_openviking_user(service, api_key=root_api_key)
        locked.services[service_name] = service
    client = docker or DockerClient()
    release = load_release_lock(release_path)
    state = load_state(state_path)
    copied_branch_data = prepare_release_data(
        service_name=service_name,
        service=service,
        locked=locked,
        release=release,
        state=state,
    )
    logger.debug(
        "lifecycle up data prepared service=%s branch_copy=%s", service_name, copied_branch_data
    )
    client.ensure_gateway_network(locked.gateway.network_name)
    client.run_backend(service_name, service)
    service = bootstrap_openviking_user(service_name, service, client)
    locked.services[service_name] = service

    state.updated_at = datetime.now(UTC)
    state.services[service_name] = RuntimeServiceState(
        candidate_release_id=service.release_id,
        online_release_id=state.services.get(service_name, RuntimeServiceState()).online_release_id,
        stable_host=service.stable_host,
        stable_port=service.stable_port,
    )
    logger.debug("writing runtime state service=%s state_path=%s", service_name, state_path)
    write_state(state, state_path)
    _reload_gateway(locked=locked, release=release, state=state, client=client)
    logger.debug("lifecycle up complete service=%s release_id=%s", service_name, service.release_id)
    return service.release_id, service.candidate_port


def _load_configured_root_api_key(locked: LockedServerConfig) -> str | None:
    try:
        config = load_user_server_config(locked.source)
    except (OSError, ValueError):
        logger.debug("shared OpenViking root key unavailable source=%s", locked.source)
        return None
    return config.defaults.openviking.root_api_key


def promote_service(
    service_name: str,
    *,
    lock_path: Path | None = None,
    release_path: Path | None = None,
    state_path: Path | None = None,
    docker: DockerClient | None = None,
) -> str:
    logger.debug(
        "lifecycle promote start service=%s lock_path=%s release_path=%s state_path=%s",
        service_name,
        lock_path,
        release_path,
        state_path,
    )
    locked = load_locked_config(lock_path)
    service = _get_locked_service(locked, service_name)
    client = docker or DockerClient()

    release = load_release_lock(release_path)
    release.updated_at = datetime.now(UTC)
    release.services[service_name] = service
    logger.debug(
        "writing release lock service=%s release_id=%s path=%s",
        service_name,
        service.release_id,
        release_path,
    )
    write_release_lock(release, release_path)

    state = load_state(state_path)
    state.updated_at = datetime.now(UTC)
    state.services[service_name] = RuntimeServiceState(
        candidate_release_id=None,
        online_release_id=service.release_id,
        stable_host=service.stable_host,
        stable_port=service.stable_port,
    )
    logger.debug("writing runtime state service=%s state_path=%s", service_name, state_path)
    write_state(state, state_path)
    _reload_gateway(locked=locked, release=release, state=state, client=client)
    logger.debug(
        "lifecycle promote complete service=%s release_id=%s", service_name, service.release_id
    )
    return service.release_id


def switch_service(
    service_name: str,
    release_id: str,
    *,
    lock_path: Path | None = None,
    release_path: Path | None = None,
    state_path: Path | None = None,
    docker: DockerClient | None = None,
) -> str:
    logger.debug(
        "lifecycle switch start service=%s release_id=%s lock_path=%s "
        "release_path=%s state_path=%s",
        service_name,
        release_id,
        lock_path,
        release_path,
        state_path,
    )
    locked = load_locked_config(lock_path)
    service = _get_locked_service(locked, service_name)
    switched = _service_for_release(service_name, service, release_id)
    client = docker or DockerClient()
    if not client.container_running(switched.release_container_name):
        raise ValueError(f"backend container is not running: {switched.release_container_name}")

    release = load_release_lock(release_path)
    release.updated_at = datetime.now(UTC)
    release.services[service_name] = switched
    logger.debug(
        "writing release lock service=%s release_id=%s path=%s",
        service_name,
        release_id,
        release_path,
    )
    write_release_lock(release, release_path)

    state = load_state(state_path)
    state.updated_at = datetime.now(UTC)
    state.services[service_name] = RuntimeServiceState(
        candidate_release_id=state.services.get(
            service_name, RuntimeServiceState()
        ).candidate_release_id,
        online_release_id=release_id,
        stable_host=service.stable_host,
        stable_port=service.stable_port,
    )
    logger.debug("writing runtime state service=%s state_path=%s", service_name, state_path)
    write_state(state, state_path)
    _reload_gateway(locked=locked, release=release, state=state, client=client)
    logger.debug("lifecycle switch complete service=%s release_id=%s", service_name, release_id)
    return release_id


def down_service(
    service_name: str,
    *,
    lock_path: Path | None = None,
    release_path: Path | None = None,
    state_path: Path | None = None,
    docker: DockerClient | None = None,
) -> None:
    logger.debug(
        "lifecycle down start service=%s lock_path=%s release_path=%s state_path=%s",
        service_name,
        lock_path,
        release_path,
        state_path,
    )
    client = docker or DockerClient()
    stable_host = None
    stable_port = None
    try:
        locked = load_locked_config(lock_path)
        service = _get_locked_service(locked, service_name)
        stable_host = service.stable_host
        stable_port = service.stable_port
        client.stop_remove(service.release_container_name)
    except (FileNotFoundError, ValueError):
        locked = None
        pass

    try:
        release = load_release_lock(release_path)
        if service_name in release.services:
            released_service = release.services[service_name]
            stable_host = stable_host or released_service.stable_host
            stable_port = stable_port or released_service.stable_port
            client.stop_remove(released_service.release_container_name)
    except FileNotFoundError:
        release = ReleaseLock(updated_at=datetime.now(UTC))
        pass

    state = load_state(state_path)
    runtime = state.services.get(service_name, RuntimeServiceState())
    state.services[service_name] = RuntimeServiceState(
        candidate_release_id=None,
        online_release_id=None,
        stable_host=runtime.stable_host or stable_host,
        stable_port=runtime.stable_port or stable_port,
    )
    state.updated_at = datetime.now(UTC)
    logger.debug("writing runtime state service=%s state_path=%s", service_name, state_path)
    write_state(state, state_path)
    if locked:
        release = load_release_lock(release_path)
        _reload_gateway(locked=locked, release=release, state=state, client=client)
    logger.debug("lifecycle down complete service=%s", service_name)


def _get_locked_service(config: LockedServerConfig, service_name: str):
    try:
        return config.services[service_name]
    except KeyError as exc:
        raise ValueError(f"unknown service: {service_name}") from exc


def _reload_gateway(
    *,
    locked: LockedServerConfig,
    release: ReleaseLock,
    state,
    client: DockerClient,
) -> None:
    logger.debug(
        "reloading gateway container=%s config_path=%s",
        locked.gateway_container_name,
        locked.gateway_config_path,
    )
    write_gateway_config(locked=locked, release=release, state=state)
    client.ensure_gateway(
        container_name=locked.gateway_container_name,
        image=locked.gateway.image,
        host=locked.gateway.host,
        port=locked.gateway.port or 80,
        network_name=locked.gateway.network_name,
        config_path=locked.gateway_config_path,
    )
    client.reload_gateway(locked.gateway_container_name)


def _service_for_release(
    service_name: str,
    service: LockedServiceSpec,
    release_id: str,
) -> LockedServiceSpec:
    if service.release_id == release_id:
        return service
    return service.model_copy(
        update={
            "release_id": release_id,
            "release_container_name": f"ov-mgn-{service_name}-{release_id}",
            "code_dir": service.code_dir.parents[1] / release_id / "code",
            "config_dir": service.config_dir.parents[1] / release_id / "config",
            "release_data_dir": service.release_data_dir.parents[1] / release_id / "data",
            "source": service.source.model_copy(
                update={"snapshot_path": service.code_dir.parents[1] / release_id / "code"}
            ),
            "openviking": service.openviking.model_copy(
                update={
                    "vars": {**service.openviking.vars, "release_id": release_id},
                    "config_file": service.config_dir.parents[1]
                    / release_id
                    / "config"
                    / "openviking.conf",
                    "cli_config_file": service.config_dir.parents[1]
                    / release_id
                    / "config"
                    / "ovcli.conf",
                }
            ),
        }
    )
