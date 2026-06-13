import json

from click.testing import CliRunner

from ov_mgn.cli import main
from ov_mgn.server_config import load_locked_config, load_release_lock, load_state


def test_candidate_promote_flow_e2e_without_real_docker(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    source_dir = workspace / "openviking-alpha"
    data_root = workspace / "data"
    secret_env_file = workspace / "secrets.env"
    config_path = workspace / "server.json"
    lock_path = workspace / "server.json.lock"
    release_path = workspace / "release.json.lock"
    state_path = workspace / "state.json"
    workspace.mkdir()
    source_dir.mkdir()
    (source_dir / "app.py").write_text("VERSION = 'candidate'\n", encoding="utf-8")
    secret_env_file.write_text("OPENVIKING_TOKEN=super-secret\n", encoding="utf-8")
    config_path.write_text(
        json.dumps(
            {
                "defaults": {
                    "port_range": [30000, 39999],
                    "image": "openviking/openviking:latest",
                    "data_root": str(workspace / "default-data"),
                    "secret_env_file": None,
                },
                "services": {
                    "alpha": {
                        "stable_host": "127.0.0.1",
                        "stable_port": 18080,
                        "source": {"type": "local", "path": str(source_dir)},
                        "openviking": {
                            "env": {},
                            "vars": {},
                        },
                    }
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    runner = CliRunner()

    set_port_range = runner.invoke(
        main,
        [
            "config-file",
            "set",
            "--config-path",
            str(config_path),
            "defaults.port_range",
            "[31000,31010]",
        ],
    )
    set_image = runner.invoke(
        main,
        [
            "config-file",
            "set",
            "--config-path",
            str(config_path),
            "defaults.image",
            "example/openviking:test",
        ],
    )
    set_data = runner.invoke(
        main,
        [
            "config-file",
            "set",
            "--config-path",
            str(config_path),
            "defaults.data_root",
            str(data_root),
        ],
    )
    set_secret = runner.invoke(
        main,
        [
            "config-file",
            "set",
            "--config-path",
            str(config_path),
            "defaults.secret_env_file",
            str(secret_env_file),
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
            "alpha-e2e",
        ],
    )
    validate = runner.invoke(main, ["config-file", "validate", "--config-path", str(config_path)])

    assert set_port_range.exit_code == 0, set_port_range.output
    assert set_image.exit_code == 0, set_image.output
    assert set_data.exit_code == 0, set_data.output
    assert set_secret.exit_code == 0, set_secret.output
    assert set_env.exit_code == 0, set_env.output
    assert set_var.exit_code == 0, set_var.output
    assert validate.exit_code == 0, validate.output

    plan = runner.invoke(
        main,
        ["plan", "--config-path", str(config_path), "--lock-path", str(lock_path)],
    )

    assert plan.exit_code == 0, plan.output
    assert lock_path.exists()
    assert "super-secret" not in lock_path.read_text(encoding="utf-8")
    locked = load_locked_config(lock_path)
    service = locked.services["alpha"]
    assert service.candidate_port == 31000
    assert service.stable_port == 18080

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

    assert up.exit_code == 0, up.output
    assert service.release_id in up.output
    assert (service.code_dir / "app.py").read_text(encoding="utf-8") == "VERSION = 'candidate'\n"
    assert 'profile = "alpha-e2e"' in service.openviking.config_file.read_text(encoding="utf-8")
    assert load_state(state_path).services["alpha"].candidate_release_id == service.release_id

    (source_dir / "app.py").write_text("VERSION = 'after-up-edit'\n", encoding="utf-8")
    service.candidate_data_dir.mkdir(parents=True, exist_ok=True)
    (service.candidate_data_dir / "kb.sqlite").write_text("candidate-data", encoding="utf-8")

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

    assert promote.exit_code == 0, promote.output
    assert promote.output.strip() == f"alpha online {service.release_id}"
    assert not service.candidate_data_dir.exists()
    assert (service.release_data_dir / "kb.sqlite").read_text(encoding="utf-8") == "candidate-data"
    assert (service.code_dir / "app.py").read_text(encoding="utf-8") == "VERSION = 'candidate'\n"
    assert load_release_lock(release_path).services["alpha"].release_id == service.release_id
    state = load_state(state_path).services["alpha"]
    assert state.candidate_release_id is None
    assert state.online_release_id == service.release_id
    assert state.stable_host == "127.0.0.1"
    assert state.stable_port == 18080

    status = runner.invoke(
        main,
        [
            "status",
            "--lock-path",
            str(lock_path),
            "--release-path",
            str(release_path),
            "--state-path",
            str(state_path),
            "--no-docker",
        ],
    )

    assert status.exit_code == 0, status.output
    payload = json.loads(status.output)
    assert payload["docker"] is None
    assert payload["lock"]["services"]["alpha"]["release_id"] == service.release_id
    assert payload["release"]["services"]["alpha"]["release_id"] == service.release_id
    assert payload["state"]["services"]["alpha"]["online_release_id"] == service.release_id
