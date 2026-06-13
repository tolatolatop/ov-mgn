import stat
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ov_mgn.server_config import (
    DEFAULT_CONFIG_TEMPLATE,
    UserServerConfig,
    get_release_lock_path,
    get_server_config_path,
    get_server_lock_path,
    get_state_path,
    is_read_only,
    load_user_server_config,
    materialize_service,
    render_locked_config,
    save_user_server_config,
    write_lock_file,
)


def test_user_level_paths_are_under_ov_mgn_dir(tmp_path) -> None:
    assert get_server_config_path(tmp_path) == tmp_path / ".ov_mgn" / "server.json"
    assert get_server_lock_path(tmp_path) == tmp_path / ".ov_mgn" / "server.json.lock"
    assert get_release_lock_path(tmp_path) == tmp_path / ".ov_mgn" / "release.json.lock"
    assert get_state_path(tmp_path) == tmp_path / ".ov_mgn" / "state.json"


def test_missing_user_config_returns_empty_default_config(tmp_path) -> None:
    config = load_user_server_config(tmp_path / ".ov_mgn" / "server.json")

    assert config == UserServerConfig()


def test_render_locked_config_applies_defaults(tmp_path) -> None:
    config_path = tmp_path / ".ov_mgn" / "server.json"
    config_path.parent.mkdir()
    config_path.write_text(
        """
        {
          "defaults": {
            "image": "example/openviking:stable",
            "data_root": "~/ov-data",
            "port_range": [31000, 31010],
            "secret_env_file": "~/.ov_mgn/secret.env"
          },
          "services": {
            "alpha": {
              "stable_host": "127.0.0.1",
              "stable_port": 18080,
              "source": {"type": "local", "path": "."},
              "openviking": {
                "vars": {"profile": "alpha-custom"},
                "env": {"TZ": "Asia/Shanghai"}
              }
            }
          }
        }
        """,
        encoding="utf-8",
    )

    config = load_user_server_config(config_path)
    locked = render_locked_config(
        config,
        source=config_path,
        generated_at=datetime(2026, 6, 13, tzinfo=UTC),
    )

    service = locked.services["alpha"]
    assert service.image == "example/openviking:stable"
    assert service.stable_port == 18080
    assert service.candidate_port == 31000
    assert service.release_id.startswith("alpha-20260613T000000-")
    assert service.candidate_container_name.startswith("ov-mgn-alpha-candidate-")
    assert service.online_container_name == "ov-mgn-alpha-online"
    assert service.network_name == "ov-mgn-alpha"
    assert service.secret_env_file.name == "secret.env"
    assert service.openviking.vars["profile"] == "alpha-custom"
    assert service.openviking.env == {"TZ": "Asia/Shanghai"}


def test_write_lock_file_is_read_only(tmp_path) -> None:
    config = UserServerConfig.model_validate(
        {
            "services": {
                "alpha": {
                    "stable_port": 18080,
                    "source": {"type": "local", "path": "."},
                }
            }
        }
    )
    locked = render_locked_config(config, source=tmp_path / ".ov_mgn" / "server.json")
    lock_path = write_lock_file(locked, tmp_path / ".ov_mgn" / "server.json.lock")

    assert lock_path.exists()
    assert is_read_only(lock_path)


def test_materialize_service_copies_local_source_and_renders_template(tmp_path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "app.py").write_text("print('ready')\n", encoding="utf-8")
    template = tmp_path / "template.conf"
    template.write_text("profile=${profile}\nrelease=${release_id}\n", encoding="utf-8")
    config = UserServerConfig.model_validate(
        {
            "defaults": {"data_root": str(tmp_path / "data")},
            "services": {
                "alpha": {
                    "stable_port": 18080,
                    "source": {"type": "local", "path": str(source_dir)},
                    "openviking": {
                        "template_path": str(template),
                        "vars": {"profile": "alpha"},
                    },
                }
            },
        }
    )

    service = render_locked_config(config).services["alpha"]
    materialize_service(service)

    assert (service.code_dir / "app.py").read_text(encoding="utf-8") == "print('ready')\n"
    assert "profile=alpha" in service.openviking.config_file.read_text(encoding="utf-8")
    assert DEFAULT_CONFIG_TEMPLATE.startswith("# Generated")


def test_lock_does_not_include_secret_file_contents(tmp_path) -> None:
    secret_file = tmp_path / "secrets.env"
    secret_file.write_text("TOKEN=super-secret\n", encoding="utf-8")
    config = UserServerConfig.model_validate(
        {
            "defaults": {"secret_env_file": str(secret_file)},
            "services": {
                "alpha": {
                    "stable_port": 18080,
                    "source": {"type": "local", "path": "."},
                }
            },
        }
    )

    locked = render_locked_config(config)

    assert str(locked.services["alpha"].secret_env_file) == str(secret_file)
    assert "super-secret" not in locked.model_dump_json()


def test_user_config_rejects_duplicate_stable_ports() -> None:
    with pytest.raises(ValidationError, match="stable_port 18080"):
        UserServerConfig.model_validate(
            {
                "services": {
                    "alpha": {"stable_port": 18080, "source": {"type": "local", "path": "."}},
                    "beta": {"stable_port": 18080, "source": {"type": "local", "path": "."}},
                }
            }
        )


def test_user_config_rejects_port_range_covering_stable_port() -> None:
    with pytest.raises(ValidationError, match="must not include stable_port"):
        UserServerConfig.model_validate(
            {
                "defaults": {"port_range": [18000, 18100]},
                "services": {
                    "alpha": {"stable_port": 18080, "source": {"type": "local", "path": "."}},
                },
            }
        )


def test_user_config_rejects_invalid_service_name() -> None:
    with pytest.raises(ValidationError, match="invalid service name"):
        UserServerConfig.model_validate(
            {
                "services": {
                    "bad name": {"stable_port": 18080, "source": {"type": "local", "path": "."}},
                }
            }
        )


def test_user_config_rejects_missing_local_source(tmp_path) -> None:
    with pytest.raises(ValidationError, match="local source path must exist"):
        UserServerConfig.model_validate(
            {
                "services": {
                    "alpha": {
                        "stable_port": 18080,
                        "source": {"type": "local", "path": str(tmp_path / "missing")},
                    },
                }
            }
        )


def test_user_config_rejects_missing_template_path(tmp_path) -> None:
    with pytest.raises(ValidationError, match="template_path must exist"):
        UserServerConfig.model_validate(
            {
                "services": {
                    "alpha": {
                        "stable_port": 18080,
                        "source": {"type": "local", "path": "."},
                        "openviking": {"template_path": str(tmp_path / "missing.conf")},
                    },
                }
            }
        )


def test_user_config_rejects_invalid_env_key() -> None:
    with pytest.raises(ValidationError, match="env keys"):
        UserServerConfig.model_validate(
            {
                "services": {
                    "alpha": {
                        "stable_port": 18080,
                        "source": {"type": "local", "path": "."},
                        "openviking": {"env": {"bad-key": "value"}},
                    },
                }
            }
        )


def test_save_user_config_writes_pretty_json_with_private_permissions(tmp_path) -> None:
    config_path = tmp_path / "server.json"
    config = UserServerConfig.model_validate(
        {
            "services": {
                "alpha": {
                    "stable_port": 18080,
                    "source": {"type": "local", "path": "."},
                }
            }
        }
    )

    save_user_server_config(config, config_path)

    text = config_path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert '\n  "defaults":' in text
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600


def test_save_user_config_is_atomic_when_replace_fails(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "server.json"
    config_path.write_text('{"original": true}\n', encoding="utf-8")
    config = UserServerConfig.model_validate(
        {
            "services": {
                "alpha": {
                    "stable_port": 18080,
                    "source": {"type": "local", "path": "."},
                }
            }
        }
    )

    def fail_replace(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr("ov_mgn.server_config.os.replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        save_user_server_config(config, config_path)

    assert config_path.read_text(encoding="utf-8") == '{"original": true}\n'
