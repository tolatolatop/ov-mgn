import json
from datetime import UTC, datetime
from pathlib import Path

from ov_mgn.docker import DockerClient, write_gateway_config
from ov_mgn.server_config import (
    LockedServerConfig,
    LockedServiceSpec,
    ReleaseLock,
    RuntimeServiceState,
    configure_openviking_user,
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
    release_path: Path | None = None,
    state_path: Path | None = None,
    docker: DockerClient | None = None,
) -> tuple[str, int]:
    locked = load_locked_config(lock_path)
    service = _get_locked_service(locked, service_name)
    if not service.enabled:
        raise ValueError(f"service {service_name} is disabled")

    materialize_service(service)
    client = docker or DockerClient()
    promote_data_dir(service)
    client.ensure_gateway_network(locked.gateway.network_name)
    client.run_backend(service_name, service)
    service = _bootstrap_openviking_user(service_name, service, client)
    locked.services[service_name] = service

    state = load_state(state_path)
    state.updated_at = datetime.now(UTC)
    state.services[service_name] = RuntimeServiceState(
        candidate_release_id=service.release_id,
        online_release_id=state.services.get(service_name, RuntimeServiceState()).online_release_id,
        stable_host=service.stable_host,
        stable_port=service.stable_port,
    )
    write_state(state, state_path)
    release = load_release_lock(release_path)
    _reload_gateway(locked=locked, release=release, state=state, client=client)
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
    _reload_gateway(locked=locked, release=release, state=state, client=client)
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
    locked = load_locked_config(lock_path)
    service = _get_locked_service(locked, service_name)
    switched = _service_for_release(service_name, service, release_id)
    client = docker or DockerClient()
    if not client.container_running(switched.release_container_name):
        raise ValueError(f"backend container is not running: {switched.release_container_name}")

    release = load_release_lock(release_path)
    release.updated_at = datetime.now(UTC)
    release.services[service_name] = switched
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
    write_state(state, state_path)
    _reload_gateway(locked=locked, release=release, state=state, client=client)
    return release_id


def down_service(
    service_name: str,
    *,
    lock_path: Path | None = None,
    release_path: Path | None = None,
    state_path: Path | None = None,
    docker: DockerClient | None = None,
) -> None:
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
    write_state(state, state_path)
    if locked:
        release = load_release_lock(release_path)
        _reload_gateway(locked=locked, release=release, state=state, client=client)


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


def _bootstrap_openviking_user(
    service_name: str,
    service: LockedServiceSpec,
    client: DockerClient,
) -> LockedServiceSpec:
    if not _openviking_root_api_key_present(service):
        return service
    if getattr(client, "dry_run", False):
        return service

    client.wait_healthy(service.release_container_name)
    client.settle(5)
    user_key = _ensure_openviking_user_key(service, client)
    return configure_openviking_user(service, api_key=user_key)


def _openviking_root_api_key_present(service: LockedServiceSpec) -> bool:
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


def _ensure_openviking_user_key(service: LockedServiceSpec, client: DockerClient) -> str:
    register = client.exec(
        service.release_container_name,
        ["ov", "admin", "register-user", "default", "default", "-o", "json"],
        capture_output=True,
        allow_failure=True,
    )
    if register and register.returncode == 0:
        return _extract_user_key(register.stdout)

    regenerate = client.exec(
        service.release_container_name,
        ["ov", "admin", "regenerate-key", "default", "default", "-o", "json"],
        capture_output=True,
    )
    if regenerate is None:
        raise RuntimeError("failed to bootstrap OpenViking user key")
    return _extract_user_key(regenerate.stdout)


def _extract_user_key(output: str) -> str:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError("OpenViking user bootstrap did not return JSON") from exc
    result = payload.get("result") if isinstance(payload, dict) else None
    user_key = result.get("user_key") if isinstance(result, dict) else None
    if not isinstance(user_key, str) or not user_key:
        raise RuntimeError("OpenViking user bootstrap response did not include user_key")
    return user_key


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
