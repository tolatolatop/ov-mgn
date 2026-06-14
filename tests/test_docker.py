import subprocess

from ov_mgn.docker import (
    GATEWAY_INFO_JSON_PATH,
    GATEWAY_INFO_PATH,
    LABEL_RELEASE_ID,
    LABEL_ROLE,
    LABEL_SERVICE,
    LABEL_STABLE_HOST,
    LABEL_STABLE_PORT,
    DockerClient,
    build_gateway_run_command,
    build_run_command,
    render_gateway_config,
    render_gateway_info,
)
from ov_mgn.server_config import (
    ReleaseLock,
    RuntimeServiceState,
    RuntimeState,
    UserServerConfig,
    render_locked_config,
)


def test_backend_run_command_includes_labels_env_file_volumes_and_no_host_port(tmp_path) -> None:
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

    command = build_run_command("alpha", service, role="backend")

    assert command[:3] == ["docker", "run", "-d"]
    assert "-p" not in command
    assert service.release_container_name in command
    assert f"{LABEL_SERVICE}=alpha" in command
    assert f"{LABEL_ROLE}=backend" in command
    assert f"{LABEL_RELEASE_ID}={service.release_id}" in command
    assert f"{LABEL_STABLE_HOST}=127.0.0.1" in command
    assert f"{LABEL_STABLE_PORT}=18080" in command
    assert "--env-file" in command
    assert f"{service.config_dir}:/app/config:ro" in command
    assert f"{service.release_data_dir}:/app/data" in command
    assert "OPENVIKING_CONFIG_FILE=/app/config/openviking.conf" in command
    assert "OPENVIKING_CLI_CONFIG_FILE=/app/config/ovcli.conf" in command
    assert any(value.startswith("PATH=/app/config/bin:/app/.venv/bin:") for value in command)
    assert "TZ=Asia/Shanghai" in command


def test_build_run_command_rejects_removed_direct_roles(tmp_path) -> None:
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

    try:
        build_run_command("alpha", service, role="online")
    except ValueError as exc:
        assert "only backend role is supported" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("online role should be rejected")


def test_gateway_backend_run_command_has_no_host_port_binding(tmp_path) -> None:
    config = UserServerConfig.model_validate(
        {
            "defaults": {
                "data_root": str(tmp_path / "data"),
                "gateway": {"enabled": True, "port": 18080},
            },
            "services": {
                "alpha": {
                    "stable_port": 18080,
                    "source": {"type": "local", "path": "."},
                }
            },
        }
    )
    service = render_locked_config(config).services["alpha"]

    command = build_run_command("alpha", service, role="backend")

    assert "-p" not in command
    assert service.release_container_name in command
    assert f"{service.release_data_dir}:/app/data" in command


def test_gateway_run_command_binds_single_entry_port(tmp_path) -> None:
    command = build_gateway_run_command(
        container_name="ov-mgn-gateway",
        image="nginx:stable-alpine",
        host="127.0.0.1",
        port=18080,
        network_name="ov-mgn-gateway",
        config_path=tmp_path / "nginx.conf",
    )

    assert "127.0.0.1:18080:80" in command
    assert "ov-mgn-gateway" in command
    assert f"{tmp_path / 'nginx.conf'}:/etc/nginx/conf.d/default.conf:ro" in command


def test_render_gateway_config_routes_stable_and_candidate_with_prefix_strip(tmp_path) -> None:
    config = UserServerConfig.model_validate(
        {
            "defaults": {
                "data_root": str(tmp_path / "data"),
                "gateway": {"enabled": True, "port": 18080},
            },
            "services": {
                "alpha": {
                    "stable_port": 18080,
                    "source": {"type": "local", "path": "."},
                }
            },
        }
    )
    locked = render_locked_config(config)
    service = locked.services["alpha"]
    state = RuntimeState(
        updated_at=locked.generated_at,
        services={
            "alpha": RuntimeServiceState(
                candidate_release_id=service.release_id,
                online_release_id=service.release_id,
            )
        },
    )

    nginx = render_gateway_config(
        locked=locked,
        release=ReleaseLock(updated_at=locked.generated_at),
        state=state,
    )

    assert "location /alpha/ {" in nginx
    assert "location /alpha/__candidate/ {" in nginx
    assert f"proxy_pass http://{service.release_container_name}:1933/;" in nginx
    assert f"location = {GATEWAY_INFO_PATH} " in nginx
    assert f"location = {GATEWAY_INFO_JSON_PATH} " in nginx


def test_render_gateway_info_lists_service_urls_and_status(tmp_path) -> None:
    config = UserServerConfig.model_validate(
        {
            "defaults": {
                "data_root": str(tmp_path / "data"),
                "gateway": {"enabled": True, "host": "127.0.0.1", "port": 18080},
            },
            "services": {
                "alpha": {
                    "source": {"type": "local", "path": "."},
                },
                "beta": {
                    "route_path": "/beta-api/",
                    "source": {"type": "local", "path": "."},
                },
            },
        }
    )
    locked = render_locked_config(config)
    alpha = locked.services["alpha"]
    beta = locked.services["beta"]
    state = RuntimeState(
        updated_at=locked.generated_at,
        services={
            "alpha": RuntimeServiceState(online_release_id=alpha.release_id),
            "beta": RuntimeServiceState(candidate_release_id=beta.release_id),
        },
    )

    info = render_gateway_info(
        locked=locked,
        release=ReleaseLock(updated_at=locked.generated_at, services={"alpha": alpha}),
        state=state,
    )

    assert info["gateway"]["info_url"] == "http://127.0.0.1:18080/__ov-mgn/"
    assert info["services"] == [
        {
            "name": "alpha",
            "status": "online",
            "route_path": "/alpha/",
            "entry_url": "http://127.0.0.1:18080/alpha/",
            "candidate_url": "http://127.0.0.1:18080/alpha/__candidate/",
            "active_release_id": alpha.release_id,
            "candidate_release_id": None,
            "enabled": True,
        },
        {
            "name": "beta",
            "status": "candidate",
            "route_path": "/beta-api/",
            "entry_url": "http://127.0.0.1:18080/beta-api/",
            "candidate_url": "http://127.0.0.1:18080/beta-api/__candidate/",
            "active_release_id": None,
            "candidate_release_id": beta.release_id,
            "enabled": True,
        },
    ]


def test_backend_port_can_be_overridden_for_legacy_images(tmp_path) -> None:
    config = UserServerConfig.model_validate(
        {
            "defaults": {
                "data_root": str(tmp_path / "data"),
                "backend_port": 8080,
                "gateway": {"enabled": True, "port": 18080},
            },
            "services": {
                "alpha": {
                    "stable_port": 18080,
                    "source": {"type": "local", "path": "."},
                }
            },
        }
    )
    locked = render_locked_config(config)
    service = locked.services["alpha"]
    state = RuntimeState(
        updated_at=locked.generated_at,
        services={
            "alpha": RuntimeServiceState(
                candidate_release_id=service.release_id,
                online_release_id=service.release_id,
            )
        },
    )

    nginx = render_gateway_config(
        locked=locked,
        release=ReleaseLock(updated_at=locked.generated_at),
        state=state,
    )

    assert "-p" not in build_run_command("alpha", service, role="backend")
    assert f"proxy_pass http://{service.release_container_name}:8080/;" in nginx


def test_ensure_gateway_network_tolerates_existing_network(monkeypatch) -> None:
    def fake_run(command, capture_output, text):
        return subprocess.CompletedProcess(command, 1, "", "network already exists")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert DockerClient().ensure_gateway_network("ov-mgn-gateway") == [
        "docker",
        "network",
        "create",
        "ov-mgn-gateway",
    ]


def test_inspect_containers_parses_labeled_docker_output(monkeypatch) -> None:
    def fake_run(command, capture_output, text):
        assert "--format" in command
        return subprocess.CompletedProcess(
            command,
            0,
            (
                "ov-mgn-alpha-online\tUp 10 seconds\t127.0.0.1:18080->8080/tcp\t"
                "alpha\tonline\talpha-release\t127.0.0.1\t18080\n"
            ),
            "",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert DockerClient().inspect_containers() == [
        {
            "name": "ov-mgn-alpha-online",
            "status": "Up 10 seconds",
            "running": True,
            "ports": "127.0.0.1:18080->8080/tcp",
            "service": "alpha",
            "role": "online",
            "release_id": "alpha-release",
            "stable_host": "127.0.0.1",
            "stable_port": 18080,
        }
    ]
