import json

from click.testing import CliRunner

from ov_mgn import __version__
from ov_mgn.cli import main


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
    config_path.write_text(
        """
        {
          "services": {
            "alpha": {
              "stable_port": 18080,
              "source": {"type": "local", "path": "."}
            }
          }
        }
        """,
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
    config_path.write_text(
        """
        {
          "services": {
            "alpha": {
              "stable_port": 18080,
              "source": {"type": "local", "path": "."}
            }
          }
        }
        """,
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
    config_path.write_text(
        """
        {
          "defaults": {"port_range": [31000, 31000]},
          "services": {
            "alpha": {
              "stable_port": 18080,
              "source": {"type": "local", "path": "."}
            }
          }
        }
        """,
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        main,
        ["plan", "--config-path", str(config_path), "--lock-path", str(lock_path)],
    )

    assert result.exit_code == 0
    assert lock_path.exists()


def test_status_command_can_skip_docker(tmp_path) -> None:
    result = CliRunner().invoke(
        main,
        [
            "status",
            "--lock-path",
            str(tmp_path / "missing.lock"),
            "--release-path",
            str(tmp_path / "release.json.lock"),
            "--state-path",
            str(tmp_path / "state.json"),
            "--no-docker",
        ],
    )

    assert result.exit_code == 0
    assert '"docker": null' in result.output


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
    bad_range = runner.invoke(
        main,
        [
            "config-file",
            "set",
            "--config-path",
            str(config_path),
            "defaults.port_range",
            "[18080,18080]",
        ],
    )

    assert bad_env.exit_code != 0
    assert "env keys" in bad_env.output
    assert missing_service.exit_code != 0
    assert "config path not found" in missing_service.output
    assert bad_range.exit_code != 0
    assert "must not include stable_port" in bad_range.output
    assert config_path.read_text(encoding="utf-8") == original


def test_config_file_set_rejects_duplicate_stable_port_without_writing(tmp_path) -> None:
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
            "services.beta.stable_port",
            "18080",
        ],
    )

    assert result.exit_code != 0
    assert "is used by both" in result.output
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
                "defaults": {"port_range": [31000, 31999]},
                "services": services,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
