import subprocess

from ov_mgn.docker import (
    LABEL_RELEASE_ID,
    LABEL_ROLE,
    LABEL_SERVICE,
    LABEL_STABLE_HOST,
    LABEL_STABLE_PORT,
    DockerClient,
    build_run_command,
)
from ov_mgn.server_config import UserServerConfig, render_locked_config


def test_candidate_run_command_includes_labels_env_file_volumes_and_temp_port(tmp_path) -> None:
    config = UserServerConfig.model_validate(
        {
            "defaults": {
                "data_root": str(tmp_path / "data"),
                "port_range": [31000, 31000],
                "secret_env_file": str(tmp_path / "secrets.env"),
            },
            "services": {
                "alpha": {
                    "stable_host": "127.0.0.1",
                    "stable_port": 18080,
                    "source": {"type": "local", "path": "."},
                    "openviking": {"env": {"TZ": "Asia/Shanghai"}},
                }
            },
        }
    )
    service = render_locked_config(config).services["alpha"]

    command = build_run_command("alpha", service, role="candidate")

    assert command[:3] == ["docker", "run", "-d"]
    assert "127.0.0.1:31000:8080" in command
    assert f"{LABEL_SERVICE}=alpha" in command
    assert f"{LABEL_ROLE}=candidate" in command
    assert f"{LABEL_RELEASE_ID}={service.release_id}" in command
    assert f"{LABEL_STABLE_HOST}=127.0.0.1" in command
    assert f"{LABEL_STABLE_PORT}=18080" in command
    assert "--env-file" in command
    assert f"{service.config_dir}:/app/config:ro" in command
    assert "TZ=Asia/Shanghai" in command


def test_online_run_command_uses_stable_port_and_release_data_dir(tmp_path) -> None:
    config = UserServerConfig.model_validate(
        {
            "defaults": {"data_root": str(tmp_path / "data")},
            "services": {
                "alpha": {
                    "stable_port": 18080,
                    "source": {"type": "local", "path": "."},
                }
            },
        }
    )
    service = render_locked_config(config).services["alpha"]

    command = build_run_command("alpha", service, role="online")

    assert "127.0.0.1:18080:8080" in command
    assert f"{service.release_data_dir}:/app/data" in command
    assert service.online_container_name in command


def test_ensure_network_tolerates_existing_network(monkeypatch, tmp_path) -> None:
    config = UserServerConfig.model_validate(
        {
            "defaults": {"data_root": str(tmp_path / "data")},
            "services": {
                "alpha": {
                    "stable_port": 18080,
                    "source": {"type": "local", "path": "."},
                }
            },
        }
    )
    service = render_locked_config(config).services["alpha"]

    def fake_run(command, capture_output, text):
        return subprocess.CompletedProcess(command, 1, "", "network already exists")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert DockerClient().ensure_network(service) == ["docker", "network", "create", "ov-mgn-alpha"]
