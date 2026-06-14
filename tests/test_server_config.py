import json
import stat
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ov_mgn.server_config import (
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
    validate_openviking_model_config,
    write_lock_file,
)


def test_user_level_paths_are_under_ov_mgn_dir(tmp_path) -> None:
    assert get_server_config_path(tmp_path) == tmp_path / ".ov_mgn" / "server.json"
    assert get_server_lock_path(tmp_path) == tmp_path / ".ov_mgn" / "server.json.lock"
    assert get_release_lock_path(tmp_path) == tmp_path / ".ov_mgn" / "release.json.lock"
    assert get_state_path(tmp_path) == tmp_path / ".ov_mgn" / "state.json.lock"


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
    assert service.backend_port == 1933
    assert service.candidate_port == 31000
    assert service.release_id.startswith("alpha-20260613T000000-")
    assert service.candidate_container_name.startswith("ov-mgn-alpha-candidate-")
    assert service.online_container_name == "ov-mgn-alpha-online"
    assert service.network_name == "ov-mgn-gateway"
    assert service.secret_env_file.name == "secret.env"
    assert service.openviking.vars["profile"] == "alpha-custom"
    assert service.openviking.env == {"TZ": "Asia/Shanghai"}


def test_secret_env_file_defaults_to_none(tmp_path) -> None:
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

    assert service.secret_env_file is None


def test_gateway_config_renders_default_routes_and_global_network(tmp_path) -> None:
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

    assert locked.gateway.enabled is True
    assert locked.gateway.port == 18080
    assert service.route_path == "/alpha/"
    assert service.network_name == "ov-mgn-gateway"
    assert service.release_container_name.startswith("ov-mgn-alpha-alpha-")


def test_gateway_config_rejects_duplicate_route_paths(tmp_path) -> None:
    with pytest.raises(ValidationError, match="route_path /kb/"):
        UserServerConfig.model_validate(
            {
                "defaults": {"gateway": {"enabled": True, "port": 18080}},
                "services": {
                    "alpha": {
                        "stable_port": 18080,
                        "route_path": "/kb/",
                        "source": {"type": "local", "path": "."},
                    },
                    "beta": {
                        "stable_port": 18080,
                        "route_path": "/kb/",
                        "source": {"type": "local", "path": "."},
                    },
                },
            }
        )


def test_gateway_config_rejects_reserved_info_route_path() -> None:
    with pytest.raises(ValidationError, match="reserved /__ov-mgn/ prefix"):
        UserServerConfig.model_validate(
            {
                "services": {
                    "alpha": {
                        "route_path": "/__ov-mgn/",
                        "source": {"type": "local", "path": "."},
                    }
                }
            }
        )


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
    model_config = _write_model_config(tmp_path)
    config = UserServerConfig.model_validate(
        {
            "defaults": {
                "data_root": str(tmp_path / "data"),
                "openviking": {"model_config_file": str(model_config)},
            },
            "services": {
                "alpha": {
                    "stable_port": 18080,
                    "source": {"type": "local", "path": str(source_dir)},
                    "openviking": {"vars": {"profile": "alpha"}},
                }
            },
        }
    )

    service = render_locked_config(config).services["alpha"]
    materialize_service(service)

    assert (service.code_dir / "app.py").read_text(encoding="utf-8") == "print('ready')\n"
    rendered = service.openviking.config_file.read_text(encoding="utf-8")
    payload = json.loads(rendered)
    assert payload["server"]["host"] == "0.0.0.0"
    assert payload["server"]["port"] == 1933
    assert payload["server"]["root_api_key"]
    assert payload["storage"]["workspace"] == "/app/data"
    assert payload["runtime"]["profile"] == "alpha"
    assert payload["embedding"]["dense"]["model"] == "text-embedding-3-small"


def test_materialize_service_renders_ovcli_config_from_root_api_key(tmp_path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    model_config = _write_model_config(tmp_path)
    config = UserServerConfig.model_validate(
        {
            "defaults": {
                "data_root": str(tmp_path / "data"),
                "backend_port": 1933,
                "openviking": {"model_config_file": str(model_config)},
            },
            "services": {
                "alpha": {
                    "stable_port": 18080,
                    "source": {"type": "local", "path": str(source_dir)},
                }
            },
        }
    )

    service = render_locked_config(config).services["alpha"]
    materialize_service(service)

    root_api_key = json.loads(service.openviking.config_file.read_text(encoding="utf-8"))["server"][
        "root_api_key"
    ]
    assert service.openviking.cli_config_file.read_text(encoding="utf-8") == (
        "{\n"
        '  "url": "http://127.0.0.1:1933",\n'
        f'  "api_key": "{root_api_key}",\n'
        '  "account": "default",\n'
        '  "user": "default"\n'
        "}\n"
    )
    assert root_api_key not in service.model_dump_json()


def test_materialize_service_preserves_existing_root_api_key(tmp_path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    model_config = _write_model_config(tmp_path)
    config = UserServerConfig.model_validate(
        {
            "defaults": {
                "data_root": str(tmp_path / "data"),
                "openviking": {"model_config_file": str(model_config)},
            },
            "services": {
                "alpha": {
                    "stable_port": 18080,
                    "source": {"type": "local", "path": str(source_dir)},
                }
            },
        }
    )
    service = render_locked_config(config).services["alpha"]
    service.openviking.config_file.parent.mkdir(parents=True)
    service.openviking.config_file.write_text(
        json.dumps({"server": {"root_api_key": "existing-root"}}),
        encoding="utf-8",
    )

    materialize_service(service)

    payload = json.loads(service.openviking.config_file.read_text(encoding="utf-8"))
    assert payload["server"]["root_api_key"] == "existing-root"


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


def test_user_config_rejects_duplicate_route_paths() -> None:
    with pytest.raises(ValidationError, match="route_path /shared/"):
        UserServerConfig.model_validate(
            {
                "services": {
                    "alpha": {
                        "route_path": "/shared/",
                        "source": {"type": "local", "path": "."},
                    },
                    "beta": {
                        "route_path": "/shared/",
                        "source": {"type": "local", "path": "."},
                    },
                }
            }
        )


def test_user_config_rejects_disabled_gateway_mode() -> None:
    with pytest.raises(ValidationError, match="only gateway mode is supported"):
        UserServerConfig.model_validate(
            {
                "defaults": {"gateway": {"enabled": False}},
                "services": {
                    "alpha": {"source": {"type": "local", "path": "."}},
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


def test_openviking_validation_rejects_template_path(tmp_path) -> None:
    template = tmp_path / "openviking.conf"
    template.write_text("{}", encoding="utf-8")
    model_config = _write_model_config(tmp_path)
    config = UserServerConfig.model_validate(
        {
            "defaults": {"openviking": {"model_config_file": str(model_config)}},
            "services": {
                "alpha": {
                    "stable_port": 18080,
                    "source": {"type": "local", "path": "."},
                    "openviking": {"template_path": str(template)},
                },
            },
        }
    )

    with pytest.raises(ValueError, match="template_path is deprecated"):
        validate_openviking_model_config(config)


def test_openviking_validation_rejects_missing_model_config(tmp_path) -> None:
    config = UserServerConfig.model_validate(
        {
            "defaults": {"openviking": {"model_config_file": str(tmp_path / "missing.json")}},
            "services": {
                "alpha": {
                    "stable_port": 18080,
                    "source": {"type": "local", "path": "."},
                },
            },
        }
    )

    with pytest.raises(ValueError, match="model_config_file must exist"):
        validate_openviking_model_config(config)


def test_openviking_validation_rejects_unknown_model_sections(tmp_path) -> None:
    model_config = tmp_path / "model.json"
    model_config.write_text(json.dumps({"server": {"port": 1}}), encoding="utf-8")
    config = UserServerConfig.model_validate(
        {
            "defaults": {"openviking": {"model_config_file": str(model_config)}},
            "services": {
                "alpha": {
                    "stable_port": 18080,
                    "source": {"type": "local", "path": "."},
                },
            },
        }
    )

    with pytest.raises(ValueError, match="may only contain top-level sections"):
        validate_openviking_model_config(config)


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


def test_user_config_rejects_managed_env_key() -> None:
    with pytest.raises(ValidationError, match="managed by ov-mgn"):
        UserServerConfig.model_validate(
            {
                "services": {
                    "alpha": {
                        "stable_port": 18080,
                        "source": {"type": "local", "path": "."},
                        "openviking": {"env": {"OPENVIKING_CONFIG_FILE": "/tmp/openviking.conf"}},
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


def _write_model_config(tmp_path):
    path = tmp_path / "model.json"
    path.write_text(
        json.dumps(
            {
                "embedding": {
                    "dense": {
                        "provider": "openai",
                        "api_key": "secret",
                        "model": "text-embedding-3-small",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return path
