from ov_mgn.lifecycle import promote_service
from ov_mgn.server_config import (
    UserServerConfig,
    load_release_lock,
    load_state,
    render_locked_config,
    write_lock_file,
)


class FakeDocker:
    def __init__(self) -> None:
        self.stopped: list[str] = []
        self.conflict_args: tuple[str, int] | None = None
        self.online_runs: list[str] = []
        self.networks: list[str] = []

    def stop_remove(self, container_name: str) -> None:
        self.stopped.append(container_name)

    def remove_online_conflicts(self, stable_host: str, stable_port: int) -> list[str]:
        self.conflict_args = (stable_host, stable_port)
        self.stopped.append("old-online-same-port")
        return ["old-online-same-port"]

    def ensure_network(self, service) -> list[str]:
        self.networks.append(service.network_name)
        return []

    def run_online(self, service_name: str, service) -> list[str]:
        self.online_runs.append(f"{service_name}:{service.release_id}")
        return []


def test_promote_moves_candidate_data_and_updates_release_and_state(tmp_path) -> None:
    config = UserServerConfig.model_validate(
        {
            "defaults": {"data_root": str(tmp_path / "data")},
            "services": {
                "alpha": {
                    "stable_host": "127.0.0.1",
                    "stable_port": 18080,
                    "source": {"type": "local", "path": "."},
                }
            },
        }
    )
    locked = render_locked_config(config)
    service = locked.services["alpha"]
    service.candidate_data_dir.mkdir(parents=True)
    (service.candidate_data_dir / "db.sqlite").write_text("data", encoding="utf-8")
    lock_path = write_lock_file(locked, tmp_path / "server.json.lock")
    release_path = tmp_path / "release.json.lock"
    state_path = tmp_path / "state.json"
    docker = FakeDocker()

    release_id = promote_service(
        "alpha",
        lock_path=lock_path,
        release_path=release_path,
        state_path=state_path,
        docker=docker,
    )

    assert release_id == service.release_id
    assert not service.candidate_data_dir.exists()
    assert (service.release_data_dir / "db.sqlite").read_text(encoding="utf-8") == "data"
    assert docker.conflict_args == ("127.0.0.1", 18080)
    assert service.candidate_container_name in docker.stopped
    assert "old-online-same-port" in docker.stopped
    assert docker.online_runs == [f"alpha:{service.release_id}"]
    assert load_release_lock(release_path).services["alpha"].release_id == service.release_id
    assert load_state(state_path).services["alpha"].online_release_id == service.release_id
