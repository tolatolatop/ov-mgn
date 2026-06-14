from datetime import UTC, datetime

from ov_mgn.server_config import (
    ReleaseLock,
    RuntimeServiceState,
    RuntimeState,
    UserServerConfig,
    render_locked_config,
)
from ov_mgn.status import build_services_summary


def test_configured_service_summary_has_internal_and_external_layers(tmp_path) -> None:
    config = _config(tmp_path)

    summary = _summary(config=config)["alpha"]

    assert summary["stage"] == "configured"
    assert summary["ok"] is True
    assert summary["internal"] == {
        "configured": True,
        "planned_release_id": None,
        "released_release_id": None,
        "state_candidate_release_id": None,
        "state_online_release_id": None,
        "branch": None,
    }
    assert summary["external"] == {
        "docker_checked": False,
        "candidate_container": None,
        "online_container": None,
        "port_bindings_ok": None,
        "backend_containers_ok": None,
        "nginx_config_ok": None,
        "gateway_container": None,
    }


def test_disabled_service_with_runtime_state_is_not_ok(tmp_path) -> None:
    config = _config(tmp_path, enabled=False)
    locked = render_locked_config(config)
    service = locked.services["alpha"]
    state = RuntimeState(
        updated_at=_now(),
        services={
            "alpha": RuntimeServiceState(
                candidate_release_id=service.release_id,
                stable_host=service.stable_host,
                stable_port=service.stable_port,
            )
        },
    )

    summary = _summary(config=config, lock=locked, state=state)["alpha"]

    assert summary["stage"] == "disabled"
    assert summary["ok"] is False
    assert "service is disabled but runtime state or containers still exist" in summary["issues"]


def test_candidate_docker_checks_running_backend_and_gateway_route(tmp_path) -> None:
    config = _config(tmp_path)
    locked = render_locked_config(config)
    service = locked.services["alpha"]
    state = RuntimeState(
        updated_at=_now(),
        services={
            "alpha": RuntimeServiceState(
                candidate_release_id=service.release_id,
                stable_host=service.stable_host,
                stable_port=service.stable_port,
            )
        },
    )
    locked.gateway_config_path.parent.mkdir(parents=True, exist_ok=True)
    locked.gateway_config_path.write_text(
        (
            "server {\n"
            "    location /alpha/__candidate/ {\n"
            f"        proxy_pass http://{service.release_container_name}:1933/;\n"
            "    }\n"
            "}\n"
        ),
        encoding="utf-8",
    )

    summary = _summary(
        config=config,
        lock=locked,
        state=state,
        docker_skipped=False,
        docker_containers=[
            {
                "name": service.release_container_name,
                "status": "Up 3 seconds",
                "running": True,
                "ports": "",
                "service": "alpha",
                "role": "backend",
                "release_id": service.release_id,
                "stable_host": service.stable_host,
                "stable_port": service.stable_port,
            },
            {
                "name": locked.gateway_container_name,
                "status": "Up 3 seconds",
                "running": True,
                "ports": "127.0.0.1:18080->80/tcp",
                "role": "gateway",
            },
        ],
    )["alpha"]

    assert summary["stage"] == "candidate"
    assert summary["ok"] is True
    assert summary["external"]["candidate_container"]["name"] == service.release_container_name
    assert summary["external"]["backend_containers_ok"] is True
    assert summary["external"]["nginx_config_ok"] is True


def test_online_gateway_route_mismatch_is_inconsistent(tmp_path) -> None:
    config = _config(tmp_path)
    locked = render_locked_config(config)
    service = locked.services["alpha"]
    release = ReleaseLock(updated_at=_now(), services={"alpha": service})
    state = RuntimeState(
        updated_at=_now(),
        services={
            "alpha": RuntimeServiceState(
                online_release_id=service.release_id,
                stable_host=service.stable_host,
                stable_port=service.stable_port,
            )
        },
    )
    locked.gateway_config_path.parent.mkdir(parents=True, exist_ok=True)
    locked.gateway_config_path.write_text(
        (
            "server {\n"
            "    location /alpha/ {\n"
            "        proxy_pass http://wrong-container:1933/;\n"
            "    }\n"
            "}\n"
        ),
        encoding="utf-8",
    )

    summary = _summary(
        config=config,
        lock=locked,
        release=release,
        state=state,
        docker_skipped=False,
        docker_containers=[
            {
                "name": service.release_container_name,
                "status": "Up 3 seconds",
                "running": True,
                "ports": "",
                "service": "alpha",
                "role": "backend",
                "release_id": service.release_id,
                "stable_host": service.stable_host,
                "stable_port": service.stable_port,
            },
            {
                "name": locked.gateway_container_name,
                "status": "Up 3 seconds",
                "running": True,
                "ports": "127.0.0.1:18080->80/tcp",
                "role": "gateway",
            },
        ],
    )["alpha"]

    assert summary["stage"] == "inconsistent"
    assert summary["ok"] is False
    assert summary["external"]["nginx_config_ok"] is False
    assert "nginx gateway config does not match runtime routes" in summary["issues"]


def test_release_lock_without_runtime_state_is_planned_not_online(tmp_path) -> None:
    config = _config(tmp_path)
    locked = render_locked_config(config)
    service = locked.services["alpha"]
    release = ReleaseLock(updated_at=_now(), services={"alpha": service})

    summary = _summary(config=config, lock=locked, release=release)["alpha"]

    assert summary["stage"] == "planned"
    assert summary["ok"] is True
    assert summary["internal"]["released_release_id"] == service.release_id


def test_branch_lineage_is_reported_in_internal_summary(tmp_path) -> None:
    config = _branch_config(tmp_path)
    locked = render_locked_config(config)

    summary = _summary(config=config, lock=locked)["beta"]

    assert summary["stage"] == "planned"
    assert summary["internal"]["branch"]["parent_service"] == "alpha"
    assert summary["internal"]["branch"]["declared_at"] == "2026-06-14T00:00:00Z"


def test_gateway_status_checks_backend_gateway_and_nginx_config(tmp_path) -> None:
    config = _gateway_config(tmp_path)
    locked = render_locked_config(config)
    service = locked.services["alpha"]
    state = RuntimeState(
        updated_at=_now(),
        services={"alpha": RuntimeServiceState(candidate_release_id=service.release_id)},
    )
    locked.gateway_config_path.parent.mkdir(parents=True)
    locked.gateway_config_path.write_text(
        (
            "server {\n"
            "    location /alpha/__candidate/ {\n"
            f"        proxy_pass http://{service.release_container_name}:1933/;\n"
            "    }\n"
            "}\n"
        ),
        encoding="utf-8",
    )

    summary = _summary(
        config=config,
        lock=locked,
        state=state,
        docker_skipped=False,
        docker_containers=[
            {
                "name": service.release_container_name,
                "status": "Up 3 seconds",
                "running": True,
                "ports": "",
                "service": "alpha",
                "role": "backend",
                "release_id": service.release_id,
            },
            {
                "name": locked.gateway_container_name,
                "status": "Up 3 seconds",
                "running": True,
                "ports": "127.0.0.1:18080->80/tcp",
                "role": "gateway",
            },
        ],
    )["alpha"]

    assert summary["stage"] == "candidate"
    assert summary["ok"] is True
    assert summary["gateway"]["enabled"] is True
    assert summary["gateway"]["info_url"] == "http://127.0.0.1:18080/__ov-mgn/"
    assert (
        summary["gateway"]["services_json_url"] == "http://127.0.0.1:18080/__ov-mgn/services.json"
    )
    assert summary["gateway"]["candidate_url"] == "http://127.0.0.1:18080/alpha/__candidate/"
    assert summary["external"]["backend_containers_ok"] is True
    assert summary["external"]["nginx_config_ok"] is True


def test_gateway_status_marks_missing_backend_inconsistent(tmp_path) -> None:
    config = _gateway_config(tmp_path)
    locked = render_locked_config(config)
    service = locked.services["alpha"]
    state = RuntimeState(
        updated_at=_now(),
        services={"alpha": RuntimeServiceState(candidate_release_id=service.release_id)},
    )

    summary = _summary(
        config=config,
        lock=locked,
        state=state,
        docker_skipped=False,
        docker_containers=[
            {
                "name": locked.gateway_container_name,
                "status": "Up 3 seconds",
                "running": True,
                "ports": "127.0.0.1:18080->80/tcp",
                "role": "gateway",
            },
        ],
    )["alpha"]

    assert summary["stage"] == "inconsistent"
    assert summary["ok"] is False
    assert summary["external"]["backend_containers_ok"] is False


def _summary(
    *,
    config: UserServerConfig,
    lock=None,
    release=None,
    state=None,
    docker_containers=None,
    docker_skipped=True,
):
    return build_services_summary(
        config=config,
        lock=lock,
        release=release or ReleaseLock(updated_at=_now()),
        state=state or RuntimeState(updated_at=_now()),
        docker_containers=docker_containers or [],
        docker_skipped=docker_skipped,
    )


def _config(tmp_path, *, enabled: bool = True) -> UserServerConfig:
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    return UserServerConfig.model_validate(
        {
            "defaults": {
                "data_root": str(tmp_path / "data"),
                "port_range": [31000, 31999],
            },
            "services": {
                "alpha": {
                    "enabled": enabled,
                    "stable_host": "127.0.0.1",
                    "stable_port": 18080,
                    "source": {"type": "local", "path": str(source)},
                }
            },
        }
    )


def _gateway_config(tmp_path) -> UserServerConfig:
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    return UserServerConfig.model_validate(
        {
            "defaults": {
                "data_root": str(tmp_path / "data"),
                "port_range": [31000, 31999],
                "gateway": {"enabled": True, "port": 18080},
            },
            "services": {
                "alpha": {
                    "stable_port": 18080,
                    "source": {"type": "local", "path": str(source)},
                }
            },
        }
    )


def _branch_config(tmp_path) -> UserServerConfig:
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    return UserServerConfig.model_validate(
        {
            "defaults": {
                "data_root": str(tmp_path / "data"),
                "port_range": [31000, 31999],
            },
            "services": {
                "alpha": {
                    "stable_port": 18080,
                    "source": {"type": "local", "path": str(source)},
                },
                "beta": {
                    "route_path": "/beta/",
                    "source": {"type": "local", "path": str(source)},
                    "branch": {
                        "parent_service": "alpha",
                        "declared_at": "2026-06-14T00:00:00Z",
                    },
                },
            },
        }
    )


def _now() -> datetime:
    return datetime(2026, 6, 14, tzinfo=UTC)
