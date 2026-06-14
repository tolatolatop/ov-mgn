import json
import subprocess
from datetime import UTC, datetime

import pytest

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
        self.execs: list[list[str]] = []
        self.health_waits: list[str] = []
        self.settles: list[float] = []
        self.backend_secret_env_files: list[str | None] = []
        self.running = True
        self.register_user_returncode = 0

    def stop_remove(self, container_name: str) -> None:
        self.stopped.append(container_name)

    def ensure_gateway_network(self, network_name: str) -> list[str]:
        self.networks.append(network_name)
        return []

    def run_backend(self, service_name: str, service) -> list[str]:
        self.backend_runs.append(f"{service_name}:{service.release_id}")
        self.backend_secret_env_files.append(
            str(service.secret_env_file) if service.secret_env_file else None
        )
        return []

    def ensure_gateway(self, **kwargs) -> list[str]:
        self.gateway_runs.append(f"{kwargs['container_name']}:{kwargs['port']}")
        return []

    def reload_gateway(self, container_name: str) -> list[str]:
        self.reloads.append(container_name)
        return []

    def container_running(self, container_name: str) -> bool:
        return self.running

    def wait_healthy(self, container_name: str, *, timeout_seconds: int = 60) -> None:
        self.health_waits.append(container_name)

    def settle(self, seconds: float) -> None:
        self.settles.append(seconds)

    def exec(
        self,
        container_name: str,
        command: list[str],
        *,
        capture_output: bool = False,
        allow_failure: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str] | None:
        self.execs.append(command)
        if "register-user" in command:
            return subprocess.CompletedProcess(
                ["docker", "exec", container_name, *command],
                self.register_user_returncode,
                stdout=json.dumps({"result": {"user_key": "user-key-from-register"}}),
                stderr="",
            )
        return subprocess.CompletedProcess(
            ["docker", "exec", container_name, *command],
            0,
            stdout=json.dumps({"result": {"user_key": "user-key-from-regenerate"}}),
            stderr="",
        )


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


def test_gateway_up_bootstraps_openviking_user_key_and_recreates_backend(tmp_path) -> None:
    user_env = tmp_path / "user.env"
    user_env.write_text("CUSTOM_SECRET=value\nVIKINGBOT_API_KEY=old\n", encoding="utf-8")
    locked = render_locked_config(_gateway_config(tmp_path, secret_env_file=user_env))
    service = locked.services["alpha"]
    lock_path = write_lock_file(locked, tmp_path / "server.json.lock")
    release_path = tmp_path / "release.json.lock"
    state_path = tmp_path / "state.json.lock"
    docker = FakeGatewayDocker()

    up_service(
        "alpha",
        lock_path=lock_path,
        release_path=release_path,
        state_path=state_path,
        docker=docker,
    )

    ovcli = json.loads(service.openviking.cli_config_file.read_text(encoding="utf-8"))
    runtime_env = (service.config_dir / "runtime.env").read_text(encoding="utf-8")
    assert docker.backend_runs == [f"alpha:{service.release_id}"]
    assert docker.stopped == []
    assert docker.health_waits == [service.release_container_name]
    assert docker.settles == [5]
    assert docker.execs == [
        ["ov", "admin", "register-user", "default", "default", "-o", "json"],
    ]
    assert docker.backend_secret_env_files == [str(user_env)]
    assert ovcli["api_key"] == "user-key-from-register"
    assert ovcli["account"] == "default"
    assert ovcli["user"] == "default"
    assert "CUSTOM_SECRET=value" in runtime_env
    assert "VIKINGBOT_ENDPOINT=http://127.0.0.1:1933/bot/v1" in runtime_env
    assert "VIKINGBOT_API_KEY" not in runtime_env
    wrapper = service.config_dir / "bin" / "ov"
    assert wrapper.exists()
    wrapper_text = wrapper.read_text(encoding="utf-8")
    assert "/app/config/openviking.conf" in wrapper_text
    assert "regenerate-key default default" in wrapper_text


def test_gateway_up_regenerates_key_when_user_already_exists(tmp_path) -> None:
    locked = render_locked_config(_gateway_config(tmp_path))
    service = locked.services["alpha"]
    lock_path = write_lock_file(locked, tmp_path / "server.json.lock")
    docker = FakeGatewayDocker()
    docker.register_user_returncode = 1

    up_service(
        "alpha",
        lock_path=lock_path,
        release_path=tmp_path / "release.json.lock",
        state_path=tmp_path / "state.json.lock",
        docker=docker,
    )

    ovcli = json.loads(service.openviking.cli_config_file.read_text(encoding="utf-8"))
    assert docker.execs == [
        ["ov", "admin", "register-user", "default", "default", "-o", "json"],
        ["ov", "admin", "regenerate-key", "default", "default", "-o", "json"],
    ]
    assert ovcli["api_key"] == "user-key-from-regenerate"


def test_branch_service_first_up_copies_parent_online_data(tmp_path) -> None:
    locked = render_locked_config(_branch_config(tmp_path))
    parent = locked.services["alpha"]
    branch = locked.services["beta"]
    parent.release_data_dir.mkdir(parents=True)
    (parent.release_data_dir / "kb.sqlite").write_text("parent-data", encoding="utf-8")
    lock_path = write_lock_file(locked, tmp_path / "server.json.lock")
    release_path = write_release_lock(
        ReleaseLock(updated_at=_now(), services={"alpha": parent}),
        tmp_path / "release.json.lock",
    )
    state_path = write_state(
        RuntimeState(
            updated_at=_now(),
            services={"alpha": RuntimeServiceState(online_release_id=parent.release_id)},
        ),
        tmp_path / "state.json.lock",
    )
    docker = FakeGatewayDocker()

    release_id, _port = up_service(
        "beta",
        lock_path=lock_path,
        release_path=release_path,
        state_path=state_path,
        docker=docker,
    )

    state = load_state(state_path)
    assert release_id == branch.release_id
    assert docker.backend_runs == [f"beta:{branch.release_id}"]
    assert (branch.release_data_dir / "kb.sqlite").read_text(encoding="utf-8") == "parent-data"
    assert state.services["alpha"].online_release_id == parent.release_id
    assert state.services["beta"].candidate_release_id == branch.release_id

    (parent.release_data_dir / "kb.sqlite").write_text("changed-parent", encoding="utf-8")
    assert (branch.release_data_dir / "kb.sqlite").read_text(encoding="utf-8") == "parent-data"


def test_branch_service_up_requires_parent_online_release(tmp_path) -> None:
    locked = render_locked_config(_branch_config(tmp_path))
    branch = locked.services["beta"]
    lock_path = write_lock_file(locked, tmp_path / "server.json.lock")

    with pytest.raises(ValueError, match="requires parent service alpha to have an online release"):
        up_service(
            "beta",
            lock_path=lock_path,
            release_path=tmp_path / "release.json.lock",
            state_path=tmp_path / "state.json.lock",
            docker=FakeGatewayDocker(),
        )

    assert not branch.release_data_dir.exists()


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


def _gateway_config(tmp_path, template_path=None, secret_env_file=None) -> UserServerConfig:
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    model_config = tmp_path / "model.json"
    model_config.write_text(
        json.dumps({"embedding": {"dense": {"provider": "openai", "api_key": "secret"}}}),
        encoding="utf-8",
    )
    defaults = {
        "data_root": str(tmp_path / "data"),
        "openviking": {"model_config_file": str(model_config)},
        "gateway": {"enabled": True, "port": 18080},
    }
    if secret_env_file:
        defaults["secret_env_file"] = str(secret_env_file)
    service = {
        "stable_port": 18080,
        "source": {"type": "local", "path": str(source)},
    }
    if template_path:
        service["openviking"] = {"template_path": str(template_path)}
    return UserServerConfig.model_validate({"defaults": defaults, "services": {"alpha": service}})


def _branch_config(tmp_path) -> UserServerConfig:
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    model_config = tmp_path / "model.json"
    model_config.write_text(
        json.dumps({"embedding": {"dense": {"provider": "openai", "api_key": "secret"}}}),
        encoding="utf-8",
    )
    return UserServerConfig.model_validate(
        {
            "defaults": {
                "data_root": str(tmp_path / "data"),
                "openviking": {"model_config_file": str(model_config)},
                "gateway": {"enabled": True, "port": 18080},
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
