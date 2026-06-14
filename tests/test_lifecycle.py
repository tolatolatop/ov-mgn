from datetime import UTC, datetime

from ov_mgn.lifecycle import down_service, promote_service, switch_service, up_service
from ov_mgn.server_config import (
    ReleaseLock,
    RuntimeServiceState,
    RuntimeState,
    UserServerConfig,
    load_release_lock,
    load_state,
    render_locked_config,
    write_lock_file,
    write_release_lock,
    write_state,
)


class FakeDocker:
    def __init__(self) -> None:
        self.stopped: list[str] = []
        self.networks: list[str] = []
        self.backend_runs: list[str] = []
        self.gateway_runs: list[str] = []
        self.reloads: list[str] = []
        self.running = True

    def stop_remove(self, container_name: str) -> None:
        self.stopped.append(container_name)

    def ensure_gateway_network(self, network_name: str) -> list[str]:
        self.networks.append(network_name)
        return []

    def run_backend(self, service_name: str, service) -> list[str]:
        self.backend_runs.append(f"{service_name}:{service.release_id}")
        return []

    def ensure_gateway(self, **kwargs) -> list[str]:
        self.gateway_runs.append(f"{kwargs['container_name']}:{kwargs['port']}")
        return []

    def reload_gateway(self, container_name: str) -> list[str]:
        self.reloads.append(container_name)
        return []

    def container_running(self, container_name: str) -> bool:
        return self.running


class FakeGatewayDocker(FakeDocker):
    pass


def test_promote_switches_gateway_route_and_updates_release_and_state(tmp_path) -> None:
    locked = render_locked_config(_gateway_config(tmp_path))
    service = locked.services["alpha"]
    service.release_data_dir.mkdir(parents=True)
    (service.release_data_dir / "db.sqlite").write_text("data", encoding="utf-8")
    lock_path = write_lock_file(locked, tmp_path / "server.json.lock")
    release_path = tmp_path / "release.json.lock"
    state_path = write_state(
        RuntimeState(
            updated_at=_now(),
            services={"alpha": RuntimeServiceState(candidate_release_id=service.release_id)},
        ),
        tmp_path / "state.json.lock",
    )
    docker = FakeDocker()

    release_id = promote_service(
        "alpha",
        lock_path=lock_path,
        release_path=release_path,
        state_path=state_path,
        docker=docker,
    )

    assert release_id == service.release_id
    assert (service.release_data_dir / "db.sqlite").read_text(encoding="utf-8") == "data"
    assert docker.stopped == []
    assert docker.gateway_runs == ["ov-mgn-gateway:18080"]
    assert docker.reloads == ["ov-mgn-gateway"]
    assert load_release_lock(release_path).services["alpha"].release_id == service.release_id
    assert load_state(state_path).services["alpha"].online_release_id == service.release_id


def test_down_stops_known_containers_and_clears_runtime_release_ids(tmp_path) -> None:
    locked = render_locked_config(_gateway_config(tmp_path))
    service = locked.services["alpha"]
    lock_path = write_lock_file(locked, tmp_path / "server.json.lock")
    release_path = write_release_lock(
        ReleaseLock(updated_at=_now(), services={"alpha": service}),
        tmp_path / "release.json.lock",
    )
    state_path = write_state(
        RuntimeState(
            updated_at=_now(),
            services={
                "alpha": RuntimeServiceState(
                    candidate_release_id=service.release_id,
                    online_release_id=service.release_id,
                    stable_host=service.stable_host,
                    stable_port=service.stable_port,
                )
            },
        ),
        tmp_path / "state.json.lock",
    )
    docker = FakeDocker()

    down_service(
        "alpha",
        lock_path=lock_path,
        release_path=release_path,
        state_path=state_path,
        docker=docker,
    )

    state = load_state(state_path).services["alpha"]
    assert docker.stopped == [service.release_container_name, service.release_container_name]
    assert docker.gateway_runs == ["ov-mgn-gateway:18080"]
    assert docker.reloads == ["ov-mgn-gateway"]
    assert state.candidate_release_id is None
    assert state.online_release_id is None
    assert state.stable_host == "127.0.0.1"
    assert state.stable_port == 18080


def test_down_tolerates_missing_lock_and_release_files(tmp_path) -> None:
    state_path = write_state(
        RuntimeState(
            updated_at=_now(),
            services={
                "alpha": RuntimeServiceState(
                    candidate_release_id="alpha-old",
                    online_release_id="alpha-old",
                    stable_host="127.0.0.1",
                    stable_port=18080,
                )
            },
        ),
        tmp_path / "state.json.lock",
    )

    down_service(
        "alpha",
        lock_path=tmp_path / "missing-server.json.lock",
        release_path=tmp_path / "missing-release.json.lock",
        state_path=state_path,
        docker=FakeDocker(),
    )

    state = load_state(state_path).services["alpha"]
    assert state.candidate_release_id is None
    assert state.online_release_id is None
    assert state.stable_host == "127.0.0.1"
    assert state.stable_port == 18080


def test_gateway_up_starts_backend_and_writes_candidate_route(tmp_path) -> None:
    locked = render_locked_config(_gateway_config(tmp_path))
    service = locked.services["alpha"]
    lock_path = write_lock_file(locked, tmp_path / "server.json.lock")
    release_path = tmp_path / "release.json.lock"
    state_path = tmp_path / "state.json.lock"
    docker = FakeGatewayDocker()

    release_id, _port = up_service(
        "alpha",
        lock_path=lock_path,
        release_path=release_path,
        state_path=state_path,
        docker=docker,
    )

    state = load_state(state_path).services["alpha"]
    nginx = locked.gateway_config_path.read_text(encoding="utf-8")
    assert release_id == service.release_id
    assert state.candidate_release_id == service.release_id
    assert docker.backend_runs == [f"alpha:{service.release_id}"]
    assert docker.gateway_runs == ["ov-mgn-gateway:18080"]
    assert docker.reloads == ["ov-mgn-gateway"]
    assert "location /alpha/__candidate/ {" in nginx
    assert "-candidate-" not in service.release_container_name


def test_gateway_promote_only_switches_route_and_state(tmp_path) -> None:
    locked = render_locked_config(_gateway_config(tmp_path))
    service = locked.services["alpha"]
    lock_path = write_lock_file(locked, tmp_path / "server.json.lock")
    release_path = tmp_path / "release.json.lock"
    state_path = write_state(
        RuntimeState(
            updated_at=_now(),
            services={"alpha": RuntimeServiceState(candidate_release_id=service.release_id)},
        ),
        tmp_path / "state.json.lock",
    )
    docker = FakeGatewayDocker()

    promote_service(
        "alpha",
        lock_path=lock_path,
        release_path=release_path,
        state_path=state_path,
        docker=docker,
    )

    state = load_state(state_path).services["alpha"]
    nginx = locked.gateway_config_path.read_text(encoding="utf-8")
    assert docker.stopped == []
    assert state.candidate_release_id is None
    assert state.online_release_id == service.release_id
    assert load_release_lock(release_path).services["alpha"].release_id == service.release_id
    assert "location /alpha/ {" in nginx
    assert "location /alpha/__candidate/ { return 404; }" in nginx


def test_gateway_switch_updates_stable_route_to_running_release(tmp_path) -> None:
    locked = render_locked_config(_gateway_config(tmp_path))
    old_release_id = "alpha-20260613T000000-old"
    lock_path = write_lock_file(locked, tmp_path / "server.json.lock")
    release_path = tmp_path / "release.json.lock"
    state_path = tmp_path / "state.json.lock"
    docker = FakeGatewayDocker()

    switched = switch_service(
        "alpha",
        old_release_id,
        lock_path=lock_path,
        release_path=release_path,
        state_path=state_path,
        docker=docker,
    )

    state = load_state(state_path).services["alpha"]
    release_service = load_release_lock(release_path).services["alpha"]
    assert switched == old_release_id
    assert state.online_release_id == old_release_id
    assert release_service.release_container_name == f"ov-mgn-alpha-{old_release_id}"
    assert (
        release_service.source.snapshot_path
        == tmp_path / "data" / "alpha" / "releases" / old_release_id / "code"
    )
    assert release_service.openviking.vars["release_id"] == old_release_id
    assert (
        f"proxy_pass http://ov-mgn-alpha-{old_release_id}:1933/;"
        in locked.gateway_config_path.read_text(encoding="utf-8")
    )


def _now() -> datetime:
    return datetime(2026, 6, 14, tzinfo=UTC)


def _gateway_config(tmp_path) -> UserServerConfig:
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    return UserServerConfig.model_validate(
        {
            "defaults": {
                "data_root": str(tmp_path / "data"),
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
