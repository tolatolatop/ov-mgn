import json
from datetime import UTC, datetime

from click.testing import CliRunner

from ov_mgn import __version__
from ov_mgn.branch_config import add_branch_service_config
from ov_mgn.cli import main
from ov_mgn.config_edit import get_config_path, parse_json_value, set_config_path, unset_config_path
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
from ov_mgn.wizard import run_edit_wizard, run_init_wizard


class FakePrompts:
    def __init__(
        self,
        *,
        inputs: list[str] | None = None,
        secrets: list[str] | None = None,
        confirms: list[bool] | None = None,
        selects: list[str] | None = None,
    ) -> None:
        self.inputs = inputs or []
        self.secrets = secrets or []
        self.confirms = confirms or []
        self.selects = selects or []

    def input(self, message: str, *, default: str = "") -> str:
        return self.inputs.pop(0) if self.inputs else default

    def path(
        self,
        message: str,
        *,
        default: str = "",
        only_directories: bool = False,
        only_files: bool = False,
        mandatory: bool = True,
    ) -> str:
        return self.inputs.pop(0) if self.inputs else default

    def secret(self, message: str, *, default: str = "") -> str:
        return self.secrets.pop(0) if self.secrets else default

    def confirm(self, message: str, *, default: bool = False) -> bool:
        return self.confirms.pop(0) if self.confirms else default

    def select(self, message: str, choices: list[str], *, default: str | None = None) -> str:
        if self.selects:
            value = self.selects.pop(0)
            assert value in choices
            return value
        assert default is not None
        return default

    def select_key(
        self,
        message: str,
        choices: list[tuple[str, str]],
        *,
        default: str | None = None,
    ) -> str:
        values = [value for value, _label in choices]
        labels = {label: value for value, label in choices}
        if self.selects:
            value = self.selects.pop(0)
            if value in labels:
                return labels[value]
            assert value in values
            return value
        assert default is not None
        return default


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


def test_verbose_logs_to_stderr_and_keeps_stdout_json() -> None:
    result = CliRunner().invoke(main, ["--verbose", "config"])

    assert result.exit_code == 0
    assert result.stdout.lstrip().startswith("{")
    assert "DEBUG [ov_mgn.cli]" in result.stderr


def test_default_and_quiet_do_not_print_debug_logs() -> None:
    default = CliRunner().invoke(main, ["config"])
    quiet = CliRunner().invoke(main, ["--quiet", "config"])

    assert default.exit_code == 0
    assert "DEBUG [ov_mgn.cli]" not in default.stderr
    assert quiet.exit_code == 0
    assert "DEBUG [ov_mgn.cli]" not in quiet.stderr


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


def test_verbose_plan_logs_do_not_include_sensitive_model_content(tmp_path) -> None:
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
        ["--verbose", "plan", "--config-path", str(config_path), "--lock-path", str(lock_path)],
    )

    assert result.exit_code == 0, result.output
    assert "DEBUG [ov_mgn" in result.stderr
    for sensitive in ("api_key", "root_api_key", "VIKINGBOT_API_KEY", "secret"):
        assert sensitive not in result.stderr


def test_plan_backfills_shared_openviking_root_key_without_locking_secret(tmp_path) -> None:
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
        ["plan", "--config-path", str(config_path), "--lock-path", str(lock_path)],
    )

    assert result.exit_code == 0, result.output
    server = json.loads(config_path.read_text(encoding="utf-8"))
    root_api_key = server["defaults"]["openviking"]["root_api_key"]
    assert root_api_key
    assert root_api_key not in lock_path.read_text(encoding="utf-8")


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


def test_wizard_help_shows_init_and_add_service() -> None:
    result = CliRunner().invoke(main, ["wizard", "--help"])

    assert result.exit_code == 0
    assert "init" in result.output
    assert "add-service" in result.output
    assert "edit" in result.output


def test_wizard_init_creates_server_and_model_config(tmp_path) -> None:
    config_path = tmp_path / "server.json"
    model_path = tmp_path / "model.json"
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    prompts = FakePrompts(
        inputs=[
            "alpha",
            str(source_dir),
            "/alpha/",
            "example/openviking:alpha",
            "https://api.example.invalid/v1",
            "text-embedding-3-small",
        ],
        secrets=[""],
        confirms=[True, True, True, False, False, True, True],
        selects=["local"],
    )

    written = run_init_wizard(prompts, config_path=config_path, model_path=model_path)

    assert written == [config_path, model_path]
    server = json.loads(config_path.read_text(encoding="utf-8"))
    alpha = server["services"]["alpha"]
    assert server["defaults"]["openviking"]["model_config_file"] == str(model_path)
    assert server["defaults"]["openviking"]["root_api_key"]
    assert alpha["source"]["type"] == "local"
    assert alpha["source"]["path"] == str(source_dir)
    assert alpha["route_path"] == "/alpha/"
    assert alpha["image"] == "example/openviking:alpha"
    assert alpha["openviking"]["env"] == {}
    assert alpha["openviking"]["vars"] == {}
    model = json.loads(model_path.read_text(encoding="utf-8"))
    assert model["embedding"]["dense"]["api_key"] == "replace-me"
    assert model["embedding"]["dense"]["api_base"] == "https://api.example.invalid/v1"


def test_wizard_init_existing_files_default_skip_preserves_files(tmp_path) -> None:
    config_path = tmp_path / "server.json"
    model_path = tmp_path / "model.json"
    config_path.write_text('{"services": {}}\n', encoding="utf-8")
    model_path.write_text('{"embedding": {"dense": {"api_key": "***"}}}\n', encoding="utf-8")
    original_config = config_path.read_text(encoding="utf-8")
    original_model = model_path.read_text(encoding="utf-8")
    prompts = FakePrompts(selects=["skip", "skip"])

    written = run_init_wizard(prompts, config_path=config_path, model_path=model_path)

    assert written == []
    assert config_path.read_text(encoding="utf-8") == original_config
    assert model_path.read_text(encoding="utf-8") == original_model


def test_wizard_init_model_failure_does_not_write_server_config(tmp_path) -> None:
    config_path = tmp_path / "server.json"
    model_path = tmp_path / "model.json"
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    prompts = FakePrompts(
        inputs=["alpha", str(source_dir)],
        confirms=[True, False, False, False, False],
        selects=["local"],
    )

    try:
        run_init_wizard(prompts, config_path=config_path, model_path=model_path)
    except ValueError as exc:
        assert "at least one" in str(exc)
    else:
        raise AssertionError("expected model validation failure")

    assert not config_path.exists()
    assert not model_path.exists()


def test_wizard_init_can_append_service_and_rewrite_model_after_confirm(tmp_path) -> None:
    config_path = tmp_path / "server.json"
    model_path = tmp_path / "model.json"
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _write_config(config_path, source_dir)
    model_path.write_text('{"embedding": {"dense": {"api_key": "***"}}}\n', encoding="utf-8")
    prompts = FakePrompts(
        inputs=[
            "beta",
            "https://example.com/openviking.git",
            "",
            "/beta/",
            "",
            "https://api.example.invalid/v1",
            "gpt-4o-mini",
        ],
        secrets=["new-secret"],
        confirms=[True, True, True, False, False, True, True],
        selects=["add-service", "git", "rewrite"],
    )

    written = run_init_wizard(prompts, config_path=config_path, model_path=model_path)

    assert written == [config_path, model_path]
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["services"]["beta"]["source"]["type"] == "git"
    assert payload["services"]["beta"]["source"]["repo"] == "https://example.com/openviking.git"
    assert payload["services"]["beta"]["branch"] is None
    model = json.loads(model_path.read_text(encoding="utf-8"))
    assert model["embedding"]["dense"]["api_key"] == "new-secret"


def test_wizard_add_service_cli_rejects_duplicate_route_without_writing(
    tmp_path, monkeypatch
) -> None:
    config_path = tmp_path / "server.json"
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _write_config(config_path, source_dir, include_beta=True)
    original = config_path.read_text(encoding="utf-8")
    prompts = FakePrompts(
        inputs=["gamma", str(source_dir), "/alpha/", "gamma", "", ""],
        confirms=[True],
        selects=["local"],
    )
    monkeypatch.setattr("ov_mgn.cli.InquirerPrompts", lambda: prompts)

    result = CliRunner().invoke(
        main,
        ["wizard", "add-service", "--config-path", str(config_path)],
    )

    assert result.exit_code != 0
    assert "route_path /alpha/" in result.output
    assert config_path.read_text(encoding="utf-8") == original


def test_wizard_add_service_cancel_write_preserves_file(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "server.json"
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _write_config(config_path, source_dir)
    original = config_path.read_text(encoding="utf-8")
    prompts = FakePrompts(
        inputs=["beta", str(source_dir)],
        confirms=[False, False],
        selects=["local"],
    )
    monkeypatch.setattr("ov_mgn.cli.InquirerPrompts", lambda: prompts)

    result = CliRunner().invoke(
        main,
        ["wizard", "add-service", "--config-path", str(config_path)],
    )

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "no changes"
    assert config_path.read_text(encoding="utf-8") == original


def test_wizard_edit_updates_server_field_from_scaffold(tmp_path) -> None:
    config_path = tmp_path / "server.json"
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _write_config(config_path, source_dir)
    prompts = FakePrompts(
        inputs=["alpha", str(source_dir), "/alpha-v2/"],
        confirms=[True, False, True],
        selects=["server", "service", "alpha", "local"],
    )

    written = run_edit_wizard(prompts, config_path=config_path)

    assert written == [config_path]
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["services"]["alpha"]["route_path"] == "/alpha-v2/"


def test_wizard_edit_updates_basic_defaults_without_advanced_prompts(tmp_path) -> None:
    config_path = tmp_path / "server.json"
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _write_config(config_path, source_dir)
    prompts = FakePrompts(
        inputs=[
            "ghcr.io/volcengine/openviking:stable",
            str(tmp_path / "data-v2"),
            str(tmp_path / "secrets.env"),
        ],
        confirms=[False, False, True],
        selects=["server", "defaults"],
    )

    written = run_edit_wizard(prompts, config_path=config_path)

    assert written == [config_path]
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["defaults"]["image"] == "ghcr.io/volcengine/openviking:stable"
    assert payload["defaults"]["data_root"] == str(tmp_path / "data-v2")
    assert payload["defaults"]["secret_env_file"] == str(tmp_path / "secrets.env")
    assert payload["defaults"]["backend_port"] == 1933
    assert payload["defaults"]["openviking"]["model_config_file"].endswith("model.json")
    assert payload["defaults"]["openviking"]["root_api_key"]
    assert payload["defaults"]["gateway"]["host"] == "127.0.0.1"
    assert payload["defaults"]["gateway"]["port"] == 18080


def test_wizard_edit_updates_advanced_defaults_when_requested(tmp_path) -> None:
    config_path = tmp_path / "server.json"
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _write_config(config_path, source_dir)
    prompts = FakePrompts(
        inputs=[
            "ghcr.io/volcengine/openviking:stable",
            str(tmp_path / "data-v2"),
            "",
            str(tmp_path / "model-v2.json"),
            "1934",
            "0.0.0.0",
            "18081",
            "nginx:1.27-alpine",
            "ov-mgn-custom",
            "32000",
            "32999",
        ],
        confirms=[True, False, True],
        selects=["server", "defaults"],
    )

    written = run_edit_wizard(prompts, config_path=config_path)

    assert written == [config_path]
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["defaults"]["backend_port"] == 1934
    assert payload["defaults"]["openviking"]["model_config_file"] == str(tmp_path / "model-v2.json")
    assert payload["defaults"]["gateway"]["host"] == "0.0.0.0"
    assert payload["defaults"]["gateway"]["port"] == 18081
    assert payload["defaults"]["gateway"]["image"] == "nginx:1.27-alpine"
    assert payload["defaults"]["gateway"]["network_name"] == "ov-mgn-custom"
    assert payload["defaults"]["port_range"] == [32000, 32999]
    assert "secret_env_file" not in payload["defaults"]


def test_wizard_edit_service_empty_image_removes_service_image(tmp_path) -> None:
    config_path = tmp_path / "server.json"
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _write_config(config_path, source_dir)
    prompts = FakePrompts(
        inputs=["alpha", str(source_dir), "/alpha/", ""],
        confirms=[True, False, True],
        selects=["server", "service", "alpha", "local"],
    )

    written = run_edit_wizard(prompts, config_path=config_path)

    assert written == [config_path]
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert "image" not in payload["services"]["alpha"]


def test_wizard_edit_invalid_server_change_preserves_file(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "server.json"
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _write_config(config_path, source_dir, include_beta=True)
    original = config_path.read_text(encoding="utf-8")
    prompts = FakePrompts(
        inputs=["beta", str(source_dir), "/alpha/"],
        confirms=[True],
        selects=["server", "service", "beta", "local"],
    )
    monkeypatch.setattr("ov_mgn.cli.InquirerPrompts", lambda: prompts)

    result = CliRunner().invoke(main, ["wizard", "edit", "--config-path", str(config_path)])

    assert result.exit_code != 0
    assert "route_path /alpha/" in result.output
    assert config_path.read_text(encoding="utf-8") == original


def test_wizard_edit_model_section_defaults_from_existing_config(tmp_path) -> None:
    model_path = _write_model_config(tmp_path)
    prompts = FakePrompts(
        inputs=["https://api.example.invalid/v2", "text-embedding-3-large"],
        confirms=[False, False, True],
        selects=["model", "embedding"],
    )

    written = run_edit_wizard(prompts, model_path=model_path)

    assert written == [model_path]
    payload = json.loads(model_path.read_text(encoding="utf-8"))
    assert payload["embedding"]["dense"]["api_base"] == "https://api.example.invalid/v2"
    assert payload["embedding"]["dense"]["api_key"] == "***"
    assert payload["embedding"]["dense"]["model"] == "text-embedding-3-large"


def test_wizard_edit_can_stage_server_and_model_before_saving(tmp_path) -> None:
    config_path = tmp_path / "server.json"
    model_path = _write_model_config(tmp_path)
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _write_config(config_path, source_dir)
    prompts = FakePrompts(
        inputs=[
            "alpha",
            str(source_dir),
            "/alpha-bulk/",
            "",
            "https://api.example.invalid/v3",
            "text-embedding-3-large",
        ],
        secrets=["bulk-secret"],
        confirms=[True, True, False, False, True, True],
        selects=["server", "service", "alpha", "local", "model", "embedding"],
    )

    written = run_edit_wizard(prompts, config_path=config_path, model_path=model_path)

    assert written == [config_path, model_path]
    server = json.loads(config_path.read_text(encoding="utf-8"))
    model = json.loads(model_path.read_text(encoding="utf-8"))
    assert server["services"]["alpha"]["route_path"] == "/alpha-bulk/"
    assert model["embedding"]["dense"]["api_base"] == "https://api.example.invalid/v3"
    assert model["embedding"]["dense"]["api_key"] == "bulk-secret"
    assert model["embedding"]["dense"]["model"] == "text-embedding-3-large"


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


def test_config_edit_helpers_read_write_and_unset_paths() -> None:
    payload = {
        "defaults": {"image": "default:1", "openviking": {}},
        "services": {"alpha": {"openviking": {"env": {}, "vars": {}}, "image": "old"}},
    }

    set_config_path(payload, "defaults.openviking.root_api_key", "shared-root")
    set_config_path(payload, "services.alpha.openviking.env.TZ", parse_json_value('"UTC"'))
    set_config_path(payload, "services.alpha.image", "new")
    assert get_config_path(payload, "defaults.openviking.root_api_key") == "shared-root"
    assert get_config_path(payload, "services.alpha.openviking.env.TZ") == "UTC"
    assert get_config_path(payload, "services.alpha.image") == "new"

    unset_config_path(payload, "services.alpha.image")
    assert "image" not in payload["services"]["alpha"]


def test_branch_config_helper_copies_service_and_sets_branch(tmp_path) -> None:
    config_path = tmp_path / "server.json"
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _write_config(config_path, source_dir)
    config = UserServerConfig.model_validate(json.loads(config_path.read_text(encoding="utf-8")))

    updated = add_branch_service_config(
        config,
        source_service="alpha",
        target_service="beta",
        route_path="/beta/",
    )

    beta = updated.services["beta"]
    assert beta.route_path == "/beta/"
    assert beta.branch is not None
    assert beta.branch.parent_service == "alpha"
    assert beta.openviking.vars["profile"] == "beta"


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
        json.dumps({"embedding": {"dense": {"provider": "openai", "api_key": "***"}}}),
        encoding="utf-8",
    )
    return model_config
