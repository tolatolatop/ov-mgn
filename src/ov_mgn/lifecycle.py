from datetime import UTC, datetime
from pathlib import Path

from ov_mgn.docker import DockerClient
from ov_mgn.server_config import (
    LockedServerConfig,
    RuntimeServiceState,
    load_locked_config,
    load_release_lock,
    load_state,
    materialize_service,
    promote_data_dir,
    write_release_lock,
    write_state,
)


def up_service(
    service_name: str,
    *,
    lock_path: Path | None = None,
    state_path: Path | None = None,
    docker: DockerClient | None = None,
) -> tuple[str, int]:
    locked = load_locked_config(lock_path)
    service = _get_locked_service(locked, service_name)
    if not service.enabled:
        raise ValueError(f"service {service_name} is disabled")

    materialize_service(service)
    client = docker or DockerClient()
    client.ensure_network(service)
    client.run_candidate(service_name, service)

    state = load_state(state_path)
    state.updated_at = datetime.now(UTC)
    state.services[service_name] = RuntimeServiceState(
        candidate_release_id=service.release_id,
        online_release_id=state.services.get(service_name, RuntimeServiceState()).online_release_id,
        stable_host=service.stable_host,
        stable_port=service.stable_port,
    )
    write_state(state, state_path)
    return service.release_id, service.candidate_port


def promote_service(
    service_name: str,
    *,
    lock_path: Path | None = None,
    release_path: Path | None = None,
    state_path: Path | None = None,
    docker: DockerClient | None = None,
) -> str:
    locked = load_locked_config(lock_path)
    service = _get_locked_service(locked, service_name)
    client = docker or DockerClient()

    client.stop_remove(service.candidate_container_name)
    promote_data_dir(service)
    client.remove_online_conflicts(service.stable_host, service.stable_port)
    client.ensure_network(service)
    client.run_online(service_name, service)

    release = load_release_lock(release_path)
    release.updated_at = datetime.now(UTC)
    release.services[service_name] = service
    write_release_lock(release, release_path)

    state = load_state(state_path)
    state.updated_at = datetime.now(UTC)
    state.services[service_name] = RuntimeServiceState(
        candidate_release_id=None,
        online_release_id=service.release_id,
        stable_host=service.stable_host,
        stable_port=service.stable_port,
    )
    write_state(state, state_path)
    return service.release_id


def down_service(
    service_name: str,
    *,
    lock_path: Path | None = None,
    release_path: Path | None = None,
    docker: DockerClient | None = None,
) -> None:
    client = docker or DockerClient()
    try:
        locked = load_locked_config(lock_path)
        service = _get_locked_service(locked, service_name)
        client.stop_remove(service.candidate_container_name)
    except FileNotFoundError:
        pass

    try:
        release = load_release_lock(release_path)
        if service_name in release.services:
            client.stop_remove(release.services[service_name].online_container_name)
    except FileNotFoundError:
        pass


def _get_locked_service(config: LockedServerConfig, service_name: str):
    try:
        return config.services[service_name]
    except KeyError as exc:
        raise ValueError(f"unknown service: {service_name}") from exc
