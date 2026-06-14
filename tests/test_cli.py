import json
from datetime import UTC, datetime

from click.testing import CliRunner

from ov_mgn import __version__
from ov_mgn.cli import main
from ov_mgn.docker import DockerClient
from ov_mgn.server_config import (
    ReleaseLock,
    RuntimeServiceState,
    RuntimeState,
    UserServerConfig,
    render_locked_config,
    write_lock_file,
    write_release_lock,
    write_state,
)


def test_version_command() -> None:
    result = CliRunner().invoke(main, ["--version"])

    assert result.exit_code == 0
    assert f"ov-mgn, version {__version__}" in result.output


def test_config_command() -> None:
    result = CliRunner().invoke(main, ["config"])

    assert result.exit_code == 0
    assert '"app_name": "ov-mgn"' in result.output


def test_config_command_loads_dotenv(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OV_MGN_APP_NAME=openviking-manager\n", encoding="utf-8")

    result = CliRunner().invoke(main, ["config", "--env-file", str(env_file)])

    assert result.exit_code == 0
    assert '"app_name": "openviking-manager"' in result.output


def test_config_command_loads_log_level_from_dotenv(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OV_MGN_LOG_LEVEL=debug\n", encoding="utf-8")

    result = CliRunner().invoke(main, ["config", "--env-file", str(env_file)])

    assert result.exit_code == 0
    assert '"log_level": "DEBUG"' in result.output


def test_cli_rejects_conflicting_log_shortcuts() -> None:
    result = CliRunner().invoke(main, ["--verbose", "--quiet", "config"])

    assert result.exit_code != 0
    assert "--verbose and --quiet cannot be used together" in result.output


def test_top_level_help_hides_server_config_group() -> None:
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    assert "config-file" in result.output
    assert "server-config" not in result.output


def test_server_config_render_command(tmp_path) -> None:
    config_path = tmp_path / "server.json"
    model_config = _write_model_config(tmp_path)
    config_path.write_text(
        json.dumps(
            {
                "defaults": {"openviking": {"model_config_file": str(model_config)}},
                "services": {
                    "alpha": {
                        "stable_port": 18080,
                        "source": {"type": "local", "path": "."},
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        main,
        ["server-config", "render", "--config-path", str(config_path)],
    )

    assert result.exit_code == 0
    assert '"online_container_name": "ov-mgn-alpha-online"' in result.output


def test_server_config_lock_command_writes_read_only_file(tmp_path) -> None:
    config_path = tmp_path / "server.json"
    lock_path = tmp_path / "server.json.lock"
    model_config = _write_model_config(tmp_path)
    config_path.write_text(
        json.dumps(
            {
                "defaults": {"openviking": {"model_config_file": str(model_config)}},
                "services": {
                    "alpha": {
                        "stable_port": 18080,
                        "source": {"type": "local", "path": "."},
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        main,
        [
            "server-config",
            "lock",
            "--config-path",
            str(config_path),
            "--lock-path",
            str(lock_path),
        ],
    )

    assert result.exit_code == 0
    assert str(lock_path) in result.output
    assert lock_path.exists()


def test_plan_command_writes_lock(tmp_path) -> None:
    config_path = tmp_path / "server.json"
    lock_path = tmp_path / "server.json.lock"
    model_config = _write_model_config(tmp_path)
    config_path.write_text(
        json.dumps(
            {
                "defaults": {
                    "port_range": [31000, 31000],
                    "openviking": {"model_config_file": str(model_config)},
                },
                "services": {
                    "alpha": {
                        "stable_port": 18080,
                        "source": {"type": "local", "path": "."},
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        main,
        ["plan", "--config-path", str(config_path), "--lock-path", str(lock_path)],
    )

    assert result.exit_code == 0
    assert lock_path.exists()


def test_status_command_can_skip_docker(tmp_path) -> None:
    config_path = tmp_path / "server.json"
    config_path.write_text('{"services": {}}\n', encoding="utf-8")
    result = CliRunner().invoke(
        main,
        [
            "status",
            "--config-path",
            str(config_path),
            "--lock-path",
            str(tmp_path / "missing.lock"),
            "--release-path",
            str(tmp_path / "release.json.lock"),
            "--state-path",
            str(tmp_path / "state.json.lock"),
            "--no-docker",
        ],
    )

    assert result.exit_code == 0
    assert '"docker": null' in result.output


def test_status_summarizes_configured_and_planned_services(tmp_path) -> None:
    config_path = tmp_path / "server.json"
    lock_path = tmp_path / "server.json.lock"
    release_path = tmp_path / "release.json.lock"
    state_path = tmp_path / "state.json.lock"
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _write_config(config_path, source_dir)
    runner = CliRunner()

    configured = runner.invoke(
        main,
        [
            "status",
            "--config-path",
            str(config_path),
            "--lock-path",
            str(lock_path),
            "--release-path",
            str(release_path),
            "--state-path",
            str(state_path),
            "--no-docker",
        ],
    )
    plan = runner.invoke(
        main,
        ["plan", "--config-path", str(config_path), "--lock-path", str(lock_path)],
    )
    planned = runner.invoke(
        main,
        [
            "status",
            "--config-path",
            str(config_path),
            "--lock-path",
            str(lock_path),
            "--release-path",
            str(release_path),
            "--state-path",
            str(state_path),
            "--no-docker",
        ],
    )

    assert configured.exit_code == 0, configured.output
    assert json.loads(configured.output)["services_summary"]["alpha"]["stage"] == "configured"
    assert plan.exit_code == 0, plan.output
    payload = json.loads(planned.output)
    assert payload["services_summary"]["alpha"]["stage"] == "planned"
    assert payload["services_summary"]["alpha"]["ok"] is True


def test_status_summarizes_candidate_and_online_services(tmp_path) -> None:
    config_path = tmp_path / "server.json"
    lock_path = tmp_path / "server.json.lock"
    release_path = tmp_path / "release.json.lock"
    state_path = tmp_path / "state.json.lock"
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _write_config(config_path, source_dir)
    runner = CliRunner()

    plan = runner.invoke(
        main,
        ["plan", "--config-path", str(config_path), "--lock-path", str(lock_path)],
    )
    up = runner.invoke(
        main,
        [
            "up",
            "alpha",
            "--lock-path",
            str(lock_path),
            "--state-path",
            str(state_path),
            "--dry-run",
        ],
    )
    candidate = runner.invoke(
        main,
        [
            "status",
            "--config-path",
            str(config_path),
            "--lock-path",
            str(lock_path),
            "--release-path",
            str(release_path),
            "--state-path",
            str(state_path),
            "--no-docker",
        ],
    )
    promote = runner.invoke(
        main,
        [
            "promote",
            "alpha",
            "--lock-path",
            str(lock_path),
            "--release-path",
            str(release_path),
            "--state-path",
            str(state_path),
            "--dry-run",
        ],
    )
    online = runner.invoke(
        main,
        [
            "status",
            "--config-path",
            str(config_path),
            "--lock-path",
            str(lock_path),
            "--release-path",
            str(release_path),
            "--state-path",
            str(state_path),
            "--no-docker",
        ],
    )

    assert plan.exit_code == 0, plan.output
    assert up.exit_code == 0, up.output
    candidate_summary = json.loads(candidate.output)["services_summary"]["alpha"]
    assert candidate_summary["stage"] == "candidate"
    assert candidate_summary["ok"] is None
    assert "docker inspection skipped" in candidate_summary["issues"]
    assert promote.exit_code == 0, promote.output
    online_summary = json.loads(online.output)["services_summary"]["alpha"]
    assert online_summary["stage"] == "online"
    assert online_summary["ok"] is None


def test_status_marks_inconsistent_candidate_release_id(tmp_path) -> None:
    config_path = tmp_path / "server.json"
    lock_path = tmp_path / "server.json.lock"
    release_path = tmp_path / "release.json.lock"
    state_path = tmp_path / "state.json.lock"
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _write_config(config_path, source_dir)
    config = UserServerConfig.model_validate(json.loads(config_path.read_text(encoding="utf-8")))
    locked = render_locked_config(config)
    write_lock_file(locked, lock_path)
    write_state(
        RuntimeState(
            updated_at=datetime(2026, 6, 14, tzinfo=UTC),
            services={
                "alpha": RuntimeServiceState(
                    candidate_release_id="alpha-wrong",
                    stable_host="127.0.0.1",
                    stable_port=18080,
                )
            },
        ),
        state_path,
    )

    result = CliRunner().invoke(
        main,
        [
            "status",
            "--config-path",
            str(config_path),
            "--lock-path",
            str(lock_path),
            "--release-path",
            str(release_path),
            "--state-path",
            str(state_path),
            "--no-docker",
        ],
    )

    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)["services_summary"]["alpha"]
    assert summary["stage"] == "inconsistent"
    assert summary["ok"] is False
    assert "candidate_release_id does not match server.json.lock" in summary["issues"]


def test_status_marks_orphaned_service(tmp_path) -> None:
    config_path = tmp_path / "server.json"
    release_path = tmp_path / "release.json.lock"
    state_path = tmp_path / "state.json.lock"
    config_path.write_text('{"services": {}}\n', encoding="utf-8")
    write_state(
        RuntimeState(
            updated_at=datetime(2026, 6, 14, tzinfo=UTC),
            services={
                "alpha": RuntimeServiceState(
                    online_release_id="alpha-old",
                    stable_host="127.0.0.1",
                    stable_port=18080,
                )
            },
        ),
        state_path,
    )

    result = CliRunner().invoke(
        main,
        [
            "status",
            "--config-path",
            str(config_path),
            "--lock-path",
            str(tmp_path / "missing.lock"),
            "--release-path",
            str(release_path),
            "--state-path",
            str(state_path),
            "--no-docker",
        ],
    )

    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)["services_summary"]["alpha"]
    assert summary["stage"] == "orphaned"
    assert summary["ok"] is False
    assert "service is not present in server.json" in summary["issues"]


def test_status_uses_structured_docker_containers(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "server.json"
    lock_path = tmp_path / "server.json.lock"
    release_path = tmp_path / "release.json.lock"
    state_path = tmp_path / "state.json.lock"
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _write_config(config_path, source_dir)
    config = UserServerConfig.model_validate(json.loads(config_path.read_text(encoding="utf-8")))
    locked = render_locked_config(config)
    service = locked.services["alpha"]
    write_lock_file(locked, lock_path)
    write_release_lock(
        ReleaseLock(updated_at=datetime(2026, 6, 14, tzinfo=UTC), services={"alpha": service}),
        release_path,
    )
    write_state(
        RuntimeState(
            updated_at=datetime(2026, 6, 14, tzinfo=UTC),
            services={
                "alpha": RuntimeServiceState(
                    candidate_release_id=service.release_id,
                    online_release_id=service.release_id,
                    stable_host=service.stable_host,
                    stable_port=service.stable_port,
                )
            },
        ),
        state_path,
    )
    locked.gateway_config_path.parent.mkdir(parents=True, exist_ok=True)
    locked.gateway_config_path.write_text(
        (
            "server {\n"
            "    location /alpha/ {\n"
            f"        proxy_pass http://{service.release_container_name}:1933/;\n"
            "    }\n"
            "    location /alpha/__candidate/ {\n"
            f"        proxy_pass http://{service.release_container_name}:1933/;\n"
            "    }\n"
            "}\n"
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(DockerClient, "inspect_status", lambda self: "docker text\n")
    monkeypatch.setattr(
        DockerClient,
        "inspect_containers",
        lambda self: [
            {
                "name": service.release_container_name,
                "status": "Up 3 seconds",
                "ports": "",
                "service": "alpha",
                "role": "backend",
                "release_id": service.release_id,
                "stable_host": service.stable_host,
                "stable_port": service.stable_port,
            },
        ],
    )
    monkeypatch.setattr(
        DockerClient,
        "inspect_gateway_container",
        lambda self, container_name="ov-mgn-gateway": {
            "name": container_name,
            "status": "Up 2 seconds",
            "running": True,
            "ports": "127.0.0.1:18080->80/tcp",
            "role": "gateway",
        },
    )

    result = CliRunner().invoke(
        main,
        [
            "status",
            "--config-path",
            str(config_path),
            "--lock-path",
            str(lock_path),
            "--release-path",
            str(release_path),
            "--state-path",
            str(state_path),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    summary = payload["services_summary"]["alpha"]
    assert payload["docker"] == "docker text\n"
    assert summary["stage"] == "candidate_pending_promotion"
    assert summary["ok"] is True
    assert summary["containers"]["other"][0]["name"] == service.release_container_name


def test_status_without_runtime_state_keeps_release_lock_planned(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "server.json"
    release_path = tmp_path / "release.json.lock"
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _write_config(config_path, source_dir)
    config = UserServerConfig.model_validate(json.loads(config_path.read_text(encoding="utf-8")))
    locked = render_locked_config(config)
    service = locked.services["alpha"]
    write_release_lock(
        ReleaseLock(updated_at=datetime(2026, 6, 14, tzinfo=UTC), services={"alpha": service}),
        release_path,
    )
    monkeypatch.setattr(DockerClient, "inspect_status", lambda self: "")
    monkeypatch.setattr(DockerClient, "inspect_containers", lambda self: [])

    result = CliRunner().invoke(
        main,
        [
            "status",
            "--config-path",
            str(config_path),
            "--release-path",
            str(release_path),
        ],
    )

    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)["services_summary"]["alpha"]
    assert summary["stage"] == "configured"
    assert summary["ok"] is True
    assert summary["internal"]["released_release_id"] == service.release_id


def test_config_file_help_only_shows_minimal_commands() -> None:
    result = CliRunner().invoke(main, ["config-file", "--help"])

    assert result.exit_code == 0
    for command in ("show", "validate", "set", "unset"):
        assert command in result.output
    for removed in (
        "init",
        "add-service",
        "remove-service",
        "set-default",
        "set-service",
        "set-source",
        "set-env",
        "unset-env",
        "set-var",
        "unset-var",
        "set-template",
        "unset-template",
        "online",
    ):
        assert removed not in result.output


def test_config_file_show_and_validate(tmp_path) -> None:
    config_path = tmp_path / "server.json"
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _write_config(config_path, source_dir)
    runner = CliRunner()

    show = runner.invoke(main, ["config-file", "show", "--config-path", str(config_path)])
    show_path = runner.invoke(
        main,
        ["config-file", "show", "--config-path", str(config_path), "services.alpha.stable_port"],
    )
    validate = runner.invoke(main, ["config-file", "validate", "--config-path", str(config_path)])

    assert show.exit_code == 0, show.output
    assert '"alpha"' in show.output
    assert show_path.exit_code == 0, show_path.output
    assert show_path.output.strip() == "18080"
    assert validate.exit_code == 0, validate.output
    assert validate.output.strip() == "valid"


def test_config_file_show_missing_path_is_clear(tmp_path) -> None:
    config_path = tmp_path / "server.json"
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _write_config(config_path, source_dir)

    result = CliRunner().invoke(
        main,
        ["config-file", "show", "--config-path", str(config_path), "services.beta.stable_port"],
    )

    assert result.exit_code != 0
    assert "config path not found: services.beta.stable_port" in result.output


def test_config_file_set_updates_existing_server_json_paths(tmp_path) -> None:
    config_path = tmp_path / "server.json"
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _write_config(config_path, source_dir, include_image=False)
    runner = CliRunner()

    set_image = runner.invoke(
        main,
        [
            "config-file",
            "set",
            "--config-path",
            str(config_path),
            "services.alpha.image",
            "custom:1",
        ],
    )
    set_default_image = runner.invoke(
        main,
        [
            "config-file",
            "set",
            "--config-path",
            str(config_path),
            "defaults.image",
            "default:1",
        ],
    )
    set_range = runner.invoke(
        main,
        [
            "config-file",
            "set",
            "--config-path",
            str(config_path),
            "defaults.port_range",
            "[31000,31999]",
        ],
    )
    set_port = runner.invoke(
        main,
        [
            "config-file",
            "set",
            "--config-path",
            str(config_path),
            "services.alpha.stable_port",
            "18081",
        ],
    )
    set_enabled = runner.invoke(
        main,
        [
            "config-file",
            "set",
            "--config-path",
            str(config_path),
            "services.alpha.enabled",
            "false",
        ],
    )
    set_env = runner.invoke(
        main,
        [
            "config-file",
            "set",
            "--config-path",
            str(config_path),
            "services.alpha.openviking.env.TZ",
            "Asia/Shanghai",
        ],
    )
    set_var = runner.invoke(
        main,
        [
            "config-file",
            "set",
            "--config-path",
            str(config_path),
            "services.alpha.openviking.vars.profile",
            "custom",
        ],
    )

    assert set_image.exit_code == 0, set_image.output
    assert set_default_image.exit_code == 0, set_default_image.output
    assert set_range.exit_code == 0, set_range.output
    assert set_port.exit_code == 0, set_port.output
    assert set_enabled.exit_code == 0, set_enabled.output
    assert set_env.exit_code == 0, set_env.output
    assert set_var.exit_code == 0, set_var.output
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["defaults"]["image"] == "default:1"
    assert payload["defaults"]["port_range"] == [31000, 31999]
    assert payload["services"]["alpha"]["image"] == "custom:1"
    assert payload["services"]["alpha"]["stable_port"] == 18081
    assert payload["services"]["alpha"]["enabled"] is False
    assert payload["services"]["alpha"]["openviking"]["env"]["TZ"] == "Asia/Shanghai"
    assert payload["services"]["alpha"]["openviking"]["vars"]["profile"] == "custom"


def test_config_file_set_failures_preserve_existing_file(tmp_path) -> None:
    config_path = tmp_path / "server.json"
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _write_config(config_path, source_dir)
    original = config_path.read_text(encoding="utf-8")
    runner = CliRunner()

    bad_env = runner.invoke(
        main,
        [
            "config-file",
            "set",
            "--config-path",
            str(config_path),
            "services.alpha.openviking.env.bad-key",
            "value",
        ],
    )
    missing_service = runner.invoke(
        main,
        [
            "config-file",
            "set",
            "--config-path",
            str(config_path),
            "services.beta.stable_port",
            "18082",
        ],
    )
    assert bad_env.exit_code != 0
    assert "env keys" in bad_env.output
    assert missing_service.exit_code != 0
    assert "config path not found" in missing_service.output
    assert config_path.read_text(encoding="utf-8") == original


def test_config_file_set_rejects_duplicate_route_path_without_writing(tmp_path) -> None:
    config_path = tmp_path / "server.json"
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _write_config(config_path, source_dir, include_beta=True)
    original = config_path.read_text(encoding="utf-8")

    result = CliRunner().invoke(
        main,
        [
            "config-file",
            "set",
            "--config-path",
            str(config_path),
            "services.beta.route_path",
            "/alpha/",
        ],
    )

    assert result.exit_code != 0
    assert "route_path /alpha/" in result.output
    assert config_path.read_text(encoding="utf-8") == original


def test_branch_command_adds_config_only_branch_service(tmp_path) -> None:
    config_path = tmp_path / "server.json"
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _write_config(config_path, source_dir)

    result = CliRunner().invoke(
        main,
        [
            "branch",
            "--config-path",
            str(config_path),
            "--route-path",
            "/beta-custom/",
            "alpha",
            "beta",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "added branch service beta from alpha"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    beta = payload["services"]["beta"]
    assert beta["route_path"] == "/beta-custom/"
    assert beta["source"] == payload["services"]["alpha"]["source"]
    assert beta["image"] == "example/openviking:alpha"
    assert beta["openviking"]["env"] == {"TZ": "UTC"}
    assert beta["openviking"]["vars"]["profile"] == "beta"
    assert beta["branch"]["parent_service"] == "alpha"
    assert "declared_at" in beta["branch"]
    assert "release_id" not in beta["branch"]


def test_branch_command_rejects_missing_source_or_existing_target_without_writing(tmp_path) -> None:
    config_path = tmp_path / "server.json"
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _write_config(config_path, source_dir, include_beta=True)
    original = config_path.read_text(encoding="utf-8")
    runner = CliRunner()

    missing_source = runner.invoke(
        main,
        ["branch", "--config-path", str(config_path), "missing", "gamma"],
    )
    existing_target = runner.invoke(
        main,
        ["branch", "--config-path", str(config_path), "alpha", "beta"],
    )

    assert missing_source.exit_code != 0
    assert "unknown source service: missing" in missing_source.output
    assert existing_target.exit_code != 0
    assert "target service already exists: beta" in existing_target.output
    assert config_path.read_text(encoding="utf-8") == original


def test_config_file_unset_removes_optional_fields_and_map_keys(tmp_path) -> None:
    config_path = tmp_path / "server.json"
    source_dir = tmp_path / "source"
    template = tmp_path / "openviking.conf.template"
    source_dir.mkdir()
    template.write_text("profile=${profile}\n", encoding="utf-8")
    _write_config(config_path, source_dir, template_path=template)
    runner = CliRunner()

    unset_image = runner.invoke(
        main,
        ["config-file", "unset", "--config-path", str(config_path), "services.alpha.image"],
    )
    unset_template = runner.invoke(
        main,
        [
            "config-file",
            "unset",
            "--config-path",
            str(config_path),
            "services.alpha.openviking.template_path",
        ],
    )
    unset_env = runner.invoke(
        main,
        [
            "config-file",
            "unset",
            "--config-path",
            str(config_path),
            "services.alpha.openviking.env.TZ",
        ],
    )
    unset_var = runner.invoke(
        main,
        [
            "config-file",
            "unset",
            "--config-path",
            str(config_path),
            "services.alpha.openviking.vars.profile",
        ],
    )

    assert unset_image.exit_code == 0, unset_image.output
    assert unset_template.exit_code == 0, unset_template.output
    assert unset_env.exit_code == 0, unset_env.output
    assert unset_var.exit_code == 0, unset_var.output
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    alpha = payload["services"]["alpha"]
    assert "image" not in alpha
    assert "template_path" not in alpha["openviking"]
    assert "TZ" not in alpha["openviking"]["env"]
    assert "profile" not in alpha["openviking"]["vars"]


def test_config_file_unset_rejects_required_fields_without_writing(tmp_path) -> None:
    config_path = tmp_path / "server.json"
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _write_config(config_path, source_dir)
    original = config_path.read_text(encoding="utf-8")
    runner = CliRunner()

    required_field = runner.invoke(
        main,
        ["config-file", "unset", "--config-path", str(config_path), "services.alpha.stable_port"],
    )
    whole_service = runner.invoke(
        main,
        ["config-file", "unset", "--config-path", str(config_path), "services.alpha"],
    )

    assert required_field.exit_code != 0
    assert "cannot be unset" in required_field.output
    assert whole_service.exit_code != 0
    assert "cannot be unset" in whole_service.output
    assert config_path.read_text(encoding="utf-8") == original


def _write_config(
    config_path,
    source_dir,
    *,
    include_beta: bool = False,
    include_image: bool = True,
    template_path=None,
) -> None:
    model_config = _write_model_config(config_path.parent)
    alpha = {
        "enabled": True,
        "stable_host": "127.0.0.1",
        "stable_port": 18080,
        "source": {"type": "local", "path": str(source_dir)},
        "openviking": {
            "env": {"TZ": "UTC"},
            "vars": {"profile": "alpha"},
        },
    }
    if include_image:
        alpha["image"] = "example/openviking:alpha"
    if template_path:
        alpha["openviking"]["template_path"] = str(template_path)
    services = {"alpha": alpha}
    if include_beta:
        services["beta"] = {
            "stable_port": 18081,
            "source": {"type": "local", "path": str(source_dir)},
        }
    config_path.write_text(
        json.dumps(
            {
                "defaults": {
                    "data_root": str(config_path.parent / "data"),
                    "port_range": [31000, 31999],
                    "openviking": {"model_config_file": str(model_config)},
                },
                "services": services,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_model_config(path):
    model_config = path / "model.json"
    model_config.write_text(
        json.dumps({"embedding": {"dense": {"provider": "openai", "api_key": "secret"}}}),
        encoding="utf-8",
    )
    return model_config
