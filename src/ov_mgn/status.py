from typing import Any

from ov_mgn.server_config import (
    LockedServerConfig,
    ReleaseLock,
    RuntimeState,
    UserServerConfig,
)


def build_services_summary(
    *,
    config: UserServerConfig,
    lock: LockedServerConfig | None,
    release: ReleaseLock,
    state: RuntimeState,
    docker_containers: list[dict[str, Any]],
    docker_skipped: bool,
) -> dict[str, Any]:
    service_names = set(config.services)
    if lock:
        service_names.update(lock.services)
    service_names.update(release.services)
    service_names.update(state.services)
    service_names.update(
        container["service"] for container in docker_containers if container.get("service")
    )

    containers_by_service: dict[str, list[dict[str, Any]]] = {}
    gateway_container = None
    for container in docker_containers:
        if container.get("role") == "gateway":
            gateway_container = container
            continue
        service_name = container.get("service")
        if isinstance(service_name, str) and service_name:
            containers_by_service.setdefault(service_name, []).append(container)

    return {
        service_name: _summarize_service_status(
            service_name=service_name,
            config=config,
            lock=lock,
            release=release,
            state=state,
            containers=containers_by_service.get(service_name, []),
            gateway_container=gateway_container,
            docker_skipped=docker_skipped,
        )
        for service_name in sorted(service_names)
    }


def _summarize_service_status(
    *,
    service_name: str,
    config: UserServerConfig,
    lock: LockedServerConfig | None,
    release: ReleaseLock,
    state: RuntimeState,
    containers: list[dict[str, Any]],
    gateway_container: dict[str, Any] | None,
    docker_skipped: bool,
) -> dict[str, Any]:
    configured = config.services.get(service_name)
    locked = lock.services.get(service_name) if lock else None
    released = release.services.get(service_name)
    runtime = state.services.get(service_name)
    candidate_release_id = runtime.candidate_release_id if runtime else None
    online_release_id = runtime.online_release_id if runtime else None
    stable_host = _first_present(
        configured.stable_host if configured else None,
        locked.stable_host if locked else None,
        released.stable_host if released else None,
        runtime.stable_host if runtime else None,
    )
    stable_port = _first_present(
        configured.stable_port if configured else None,
        locked.stable_port if locked else None,
        released.stable_port if released else None,
        runtime.stable_port if runtime else None,
    )
    issues: list[str] = []

    if configured is None:
        issues.append("service is not present in server.json")
    if candidate_release_id and locked and candidate_release_id != locked.release_id:
        issues.append("candidate_release_id does not match server.json.lock")
    if candidate_release_id and not locked:
        issues.append("candidate_release_id exists but server.json.lock is missing the service")
    if online_release_id and released and online_release_id != released.release_id:
        issues.append("online_release_id does not match release.json.lock")
    if online_release_id and not released:
        issues.append("online_release_id exists but release.json.lock is missing the service")

    if (
        configured
        and not configured.enabled
        and (candidate_release_id or online_release_id or containers)
    ):
        issues.append("service is disabled but runtime state or containers still exist")

    external = _gateway_external_summary(
        service_name=service_name,
        locked=locked,
        released=released,
        runtime=runtime,
        containers=containers,
        gateway_container=gateway_container,
        gateway_config_path=lock.gateway_config_path if lock else None,
        docker_skipped=docker_skipped,
        issues=issues,
    )

    stage = _service_stage(
        configured=configured is not None,
        enabled=configured.enabled if configured else True,
        locked=locked is not None,
        candidate_release_id=candidate_release_id,
        online_release_id=online_release_id,
        issues=issues,
    )
    ok = _service_ok(stage=stage, issues=issues, docker_skipped=docker_skipped)

    return {
        "stage": stage,
        "ok": ok,
        "issues": issues,
        "stable_host": stable_host,
        "stable_port": stable_port,
        "candidate_release_id": candidate_release_id,
        "online_release_id": online_release_id,
        "containers": _group_containers(containers),
        "internal": {
            "configured": configured is not None,
            "planned_release_id": locked.release_id if locked else None,
            "released_release_id": released.release_id if released else None,
            "state_candidate_release_id": candidate_release_id,
            "state_online_release_id": online_release_id,
        },
        "external": external,
        "gateway": _gateway_summary(
            gateway=lock.gateway if lock else config.defaults.gateway,
            locked=locked,
            released=released,
            runtime=runtime,
            gateway_container=gateway_container,
            nginx_config_ok=external.get("nginx_config_ok"),
        ),
    }


def _gateway_external_summary(
    *,
    service_name: str,
    locked,
    released,
    runtime,
    containers: list[dict[str, Any]],
    gateway_container: dict[str, Any] | None,
    gateway_config_path,
    docker_skipped: bool,
    issues: list[str],
) -> dict[str, Any]:
    summary = {
        "docker_checked": not docker_skipped,
        "candidate_container": None,
        "online_container": None,
        "port_bindings_ok": None,
        "backend_containers_ok": None if docker_skipped else True,
        "nginx_config_ok": None if docker_skipped else True,
        "gateway_container": gateway_container,
    }
    expected = _expected_gateway_backends(
        service_name=service_name,
        locked=locked,
        released=released,
        runtime=runtime,
    )
    if docker_skipped:
        if expected:
            issues.append("docker inspection skipped")
        return summary
    if gateway_container is None:
        issues.append("expected gateway container was not found")
        summary["backend_containers_ok"] = False
    elif not _container_running(gateway_container):
        issues.append("expected gateway container is not running")
        summary["backend_containers_ok"] = False
    for role, release_id, container_name, _backend_port in expected:
        container = _find_container(containers=containers, role="backend", release_id=release_id)
        if container is None:
            issues.append(
                f"expected {role} backend container for release {release_id} was not found"
            )
            summary["backend_containers_ok"] = False
            continue
        summary[f"{role}_container"] = container
        if container.get("name") != container_name:
            issues.append(f"expected {role} backend container name {container_name} was not found")
            summary["backend_containers_ok"] = False
        if not _container_running(container):
            issues.append(f"expected {role} backend container is not running")
            summary["backend_containers_ok"] = False
    if locked and runtime and gateway_config_path:
        nginx_ok = _nginx_config_matches(locked, expected, gateway_config_path)
        summary["nginx_config_ok"] = nginx_ok
        if not nginx_ok:
            issues.append("nginx gateway config does not match runtime routes")
    return summary


def _gateway_summary(
    *,
    gateway,
    locked,
    released,
    runtime,
    gateway_container: dict[str, Any] | None,
    nginx_config_ok: bool | None,
) -> dict[str, Any]:
    route_path = locked.route_path if locked else None
    if not route_path and released:
        route_path = released.route_path
    entry_url = None
    candidate_url = None
    info_url = None
    services_json_url = None
    if gateway.enabled and route_path and gateway.port:
        base_url = f"http://{gateway.host}:{gateway.port}"
        entry_url = f"{base_url}{route_path}"
        candidate_url = f"{base_url}{route_path}__candidate/"
        info_url = f"{base_url}/__ov-mgn/"
        services_json_url = f"{base_url}/__ov-mgn/services.json"
    return {
        "enabled": gateway.enabled,
        "entry_url": entry_url,
        "candidate_url": candidate_url,
        "info_url": info_url,
        "services_json_url": services_json_url,
        "route_path": route_path,
        "active_release_id": runtime.online_release_id if runtime else None,
        "candidate_release_id": runtime.candidate_release_id if runtime else None,
        "nginx_config_ok": nginx_config_ok,
        "gateway_container": gateway_container,
    }


def _expected_gateway_backends(
    *,
    service_name: str,
    locked,
    released,
    runtime,
) -> list[tuple[str, str, str, int]]:
    if not runtime:
        return []
    expected: list[tuple[str, str, str, int]] = []
    if runtime.candidate_release_id and locked:
        expected.append(
            (
                "candidate",
                runtime.candidate_release_id,
                locked.release_container_name,
                locked.backend_port,
            )
        )
    if runtime.online_release_id:
        source = (
            released if released and released.release_id == runtime.online_release_id else locked
        )
        if source:
            expected.append(
                (
                    "online",
                    runtime.online_release_id,
                    source.release_container_name,
                    source.backend_port,
                )
            )
    return expected


def _nginx_config_matches(locked, expected: list[tuple[str, str, str, int]], config_path) -> bool:
    if not config_path.exists():
        return False
    text = config_path.read_text(encoding="utf-8")
    for role, _release_id, container_name, backend_port in expected:
        path = locked.route_path if role == "online" else f"{locked.route_path}__candidate/"
        if f"location {path} " not in text:
            return False
        if f"proxy_pass http://{container_name}:{backend_port}/;" not in text:
            return False
    return True


def _service_stage(
    *,
    configured: bool,
    enabled: bool,
    locked: bool,
    candidate_release_id: str | None,
    online_release_id: str | None,
    issues: list[str],
) -> str:
    hard_issues = [issue for issue in issues if issue != "docker inspection skipped"]
    if hard_issues:
        if not configured:
            return "orphaned"
        if not enabled:
            return "disabled"
        return "inconsistent"
    if not configured:
        return "orphaned"
    if not enabled:
        return "disabled"
    if candidate_release_id and online_release_id:
        return "candidate_pending_promotion"
    if candidate_release_id:
        return "candidate"
    if online_release_id:
        return "online"
    if locked:
        return "planned"
    return "configured"


def _service_ok(*, stage: str, issues: list[str], docker_skipped: bool) -> bool | None:
    hard_issues = [issue for issue in issues if issue != "docker inspection skipped"]
    if hard_issues or stage in {"orphaned", "inconsistent"}:
        return False
    if docker_skipped and stage in {"candidate", "candidate_pending_promotion", "online"}:
        return None
    return True


def _find_container(
    *,
    containers: list[dict[str, Any]],
    role: str,
    release_id: str,
) -> dict[str, Any] | None:
    for container in containers:
        if container.get("role") == role and container.get("release_id") == release_id:
            return container
    return None


def _container_running(container: dict[str, Any]) -> bool:
    running = container.get("running")
    if isinstance(running, bool):
        return running
    status = container.get("status")
    return isinstance(status, str) and status.startswith("Up ")


def _group_containers(containers: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {"candidate": [], "online": [], "other": []}
    for container in containers:
        role = container.get("role")
        if role not in {"candidate", "online"}:
            role = "other"
        grouped[role].append(container)
    return grouped


def _first_present(*values):
    for value in values:
        if value is not None:
            return value
    return None
