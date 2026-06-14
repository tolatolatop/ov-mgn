import json
from pathlib import Path
from typing import Any

import click
from pydantic import ValidationError

from ov_mgn import __version__
from ov_mgn.branch_config import add_branch_service_config
from ov_mgn.config import AppConfig, LogLevel, load_config
from ov_mgn.config_edit import (
    ConfigPathError,
    get_config_path,
    parse_json_value,
    set_config_path,
    unset_config_path,
)
from ov_mgn.docker import DockerClient
from ov_mgn.lifecycle import down_service, promote_service, switch_service, up_service
from ov_mgn.logging import configure_logging, get_logger
from ov_mgn.server_config import (
    UserServerConfig,
    get_release_lock_path,
    get_server_config_path,
    get_server_lock_path,
    get_state_path,
    load_locked_config,
    load_release_lock,
    load_state,
    load_user_server_config,
    render_locked_config,
    save_user_server_config,
    save_user_server_config_data,
    validate_openviking_model_config,
    write_lock_file,
)
from ov_mgn.status import build_services_summary

logger = get_logger(__name__)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(version=__version__, prog_name="ov-mgn")
@click.option(
    "--log-level",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], case_sensitive=False),
    default=None,
    help="Logging level. Overrides OV_MGN_LOG_LEVEL.",
)
@click.option("-v", "--verbose", is_flag=True, help="Shortcut for --log-level DEBUG.")
@click.option("-q", "--quiet", is_flag=True, help="Shortcut for --log-level WARNING.")
@click.pass_context
def main(
    ctx: click.Context,
    log_level: LogLevel | None,
    verbose: bool,
    quiet: bool,
) -> None:
    """OpenViking management command line interface."""
    config = load_config()
    resolved_log_level = _resolve_log_level(
        configured=config.log_level,
        override=log_level,
        verbose=verbose,
        quiet=quiet,
    )
    configure_logging(resolved_log_level)
    logger.debug(
        "CLI entry invoked_subcommand=%s configured_log_level=%s resolved_log_level=%s",
        ctx.invoked_subcommand,
        config.log_level,
        resolved_log_level,
    )
    ctx.obj = {"config": config.model_copy(update={"log_level": resolved_log_level})}


@main.command("config")
@click.option(
    "--env-file",
    type=click.Path(dir_okay=False, path_type=str),
    default=".env",
    show_default=True,
    help="Path to the dotenv file.",
)
def show_config(env_file: str) -> None:
    """Print the loaded application configuration."""
    logger.debug("command config env_file=%s", env_file)
    config = load_config(env_file=env_file)
    logger.debug("Loaded configuration from %s", env_file)
    click.echo(_to_json(config))


@main.command("plan")
@click.option(
    "--config-path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Path to server.json. Defaults to ~/.ov_mgn/server.json.",
)
@click.option(
    "--lock-path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Path to server.json.lock. Defaults to ~/.ov_mgn/server.json.lock.",
)
def plan(config_path: Path | None, lock_path: Path | None) -> None:
    """Generate the read-only gateway deployment lock."""
    logger.debug("command plan config_path=%s lock_path=%s", config_path, lock_path)
    click.echo(str(_write_plan(config_path, lock_path)))


@main.command("up")
@click.argument("service")
@click.option("--lock-path", type=click.Path(dir_okay=False, path_type=Path), default=None)
@click.option("--release-path", type=click.Path(dir_okay=False, path_type=Path), default=None)
@click.option("--state-path", type=click.Path(dir_okay=False, path_type=Path), default=None)
@click.option("--dry-run", is_flag=True, help="Build files and state without running Docker.")
def up(
    service: str,
    lock_path: Path | None,
    release_path: Path | None,
    state_path: Path | None,
    dry_run: bool,
) -> None:
    """Start a candidate backend container for SERVICE."""
    logger.debug(
        "command up service=%s lock_path=%s release_path=%s state_path=%s dry_run=%s",
        service,
        lock_path,
        release_path,
        state_path,
        dry_run,
    )
    release_id, _port = up_service(
        service,
        lock_path=lock_path,
        release_path=release_path,
        state_path=state_path,
        docker=DockerClient(dry_run=dry_run),
    )
    locked = load_locked_config(lock_path)
    locked_service = locked.services[service]
    url = (
        f"http://{locked.gateway.host}:{locked.gateway.port}{locked_service.route_path}__candidate/"
    )
    click.echo(f"{service} candidate {release_id} preview {url}")


@main.command("promote")
@click.argument("service")
@click.option("--lock-path", type=click.Path(dir_okay=False, path_type=Path), default=None)
@click.option("--release-path", type=click.Path(dir_okay=False, path_type=Path), default=None)
@click.option("--state-path", type=click.Path(dir_okay=False, path_type=Path), default=None)
@click.option("--dry-run", is_flag=True, help="Update files without running Docker.")
def promote(
    service: str,
    lock_path: Path | None,
    release_path: Path | None,
    state_path: Path | None,
    dry_run: bool,
) -> None:
    """Promote SERVICE candidate to the stable gateway route."""
    logger.debug(
        "command promote service=%s lock_path=%s release_path=%s state_path=%s dry_run=%s",
        service,
        lock_path,
        release_path,
        state_path,
        dry_run,
    )
    release_id = promote_service(
        service,
        lock_path=lock_path,
        release_path=release_path,
        state_path=state_path,
        docker=DockerClient(dry_run=dry_run),
    )
    click.echo(f"{service} online {release_id}")


@main.command("switch")
@click.argument("service")
@click.argument("release_id")
@click.option("--lock-path", type=click.Path(dir_okay=False, path_type=Path), default=None)
@click.option("--release-path", type=click.Path(dir_okay=False, path_type=Path), default=None)
@click.option("--state-path", type=click.Path(dir_okay=False, path_type=Path), default=None)
@click.option("--dry-run", is_flag=True, help="Update files without running Docker checks.")
def switch(
    service: str,
    release_id: str,
    lock_path: Path | None,
    release_path: Path | None,
    state_path: Path | None,
    dry_run: bool,
) -> None:
    """Switch SERVICE stable gateway route to RELEASE_ID."""
    logger.debug(
        "command switch service=%s release_id=%s lock_path=%s release_path=%s state_path=%s "
        "dry_run=%s",
        service,
        release_id,
        lock_path,
        release_path,
        state_path,
        dry_run,
    )
    switched_release = switch_service(
        service,
        release_id,
        lock_path=lock_path,
        release_path=release_path,
        state_path=state_path,
        docker=DockerClient(dry_run=dry_run),
    )
    click.echo(f"{service} switched {switched_release}")


@main.command("down")
@click.argument("service")
@click.option("--lock-path", type=click.Path(dir_okay=False, path_type=Path), default=None)
@click.option("--release-path", type=click.Path(dir_okay=False, path_type=Path), default=None)
@click.option("--state-path", type=click.Path(dir_okay=False, path_type=Path), default=None)
@click.option("--dry-run", is_flag=True, help="Do not run Docker commands.")
def down(
    service: str,
    lock_path: Path | None,
    release_path: Path | None,
    state_path: Path | None,
    dry_run: bool,
) -> None:
    """Stop SERVICE backend containers known to ov-mgn."""
    logger.debug(
        "command down service=%s lock_path=%s release_path=%s state_path=%s dry_run=%s",
        service,
        lock_path,
        release_path,
        state_path,
        dry_run,
    )
    down_service(
        service,
        lock_path=lock_path,
        release_path=release_path,
        state_path=state_path,
        docker=DockerClient(dry_run=dry_run),
    )
    click.echo(f"{service} stopped")


@main.command("branch")
@click.argument("source_service")
@click.argument("target_service")
@click.option("--route-path", type=str, default=None, help="Route path for the target service.")
@click.option(
    "--config-path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Path to server.json. Defaults to ~/.ov_mgn/server.json.",
)
def branch_service_config(
    source_service: str,
    target_service: str,
    route_path: str | None,
    config_path: Path | None,
) -> None:
    """Add TARGET_SERVICE as a config branch of SOURCE_SERVICE."""
    logger.debug(
        "command branch source_service=%s target_service=%s route_path=%s config_path=%s",
        source_service,
        target_service,
        route_path,
        config_path,
    )
    config = _load_user_config_or_fail(config_path)
    try:
        updated = add_branch_service_config(
            config,
            source_service=source_service,
            target_service=target_service,
            route_path=route_path,
        )
        save_user_server_config(updated, config_path)
    except (OSError, ValidationError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"added branch service {target_service} from {source_service}")


@main.command("status")
@click.option("--config-path", type=click.Path(dir_okay=False, path_type=Path), default=None)
@click.option("--lock-path", type=click.Path(dir_okay=False, path_type=Path), default=None)
@click.option("--release-path", type=click.Path(dir_okay=False, path_type=Path), default=None)
@click.option("--state-path", type=click.Path(dir_okay=False, path_type=Path), default=None)
@click.option("--no-docker", is_flag=True, help="Skip Docker inspection.")
def status(
    config_path: Path | None,
    lock_path: Path | None,
    release_path: Path | None,
    state_path: Path | None,
    no_docker: bool,
) -> None:
    """Print lock, release, state and Docker status."""
    logger.debug(
        "command status config_path=%s lock_path=%s release_path=%s state_path=%s no_docker=%s",
        config_path,
        lock_path,
        release_path,
        state_path,
        no_docker,
    )
    payload: dict[str, object] = {
        "paths": {
            "config": str(config_path or get_server_config_path()),
            "lock": str(lock_path or get_server_lock_path()),
            "release": str(release_path or get_release_lock_path()),
            "state": str(state_path or get_state_path()),
        }
    }
    config = _load_user_config_or_fail(config_path)
    try:
        lock = load_locked_config(lock_path)
    except FileNotFoundError:
        lock = None
        payload["lock"] = None
    else:
        payload["lock"] = lock.model_dump(mode="json")
    release = load_release_lock(release_path)
    state = load_state(state_path)
    docker_client = DockerClient()
    docker_containers = [] if no_docker else docker_client.inspect_containers()
    logger.debug(
        "status docker skipped=%s discovered_containers=%d",
        no_docker,
        len(docker_containers),
    )
    if not no_docker and lock:
        gateway_container = docker_client.inspect_gateway_container(lock.gateway_container_name)
        if gateway_container:
            docker_containers.append(gateway_container)
        logger.debug("status gateway_present=%s", bool(gateway_container))
    payload["release"] = release.model_dump(mode="json")
    payload["state"] = state.model_dump(mode="json")
    payload["docker"] = None if no_docker else docker_client.inspect_status()
    payload["services_summary"] = build_services_summary(
        config=config,
        lock=lock,
        release=release,
        state=state,
        docker_containers=docker_containers,
        docker_skipped=no_docker,
    )
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@main.group("config-file")
def config_file() -> None:
    """Inspect and edit server.json."""


@config_file.command("show")
@click.option("--config-path", type=click.Path(dir_okay=False, path_type=Path), default=None)
@click.argument("path", required=False)
def show_config_file(config_path: Path | None, path: str | None) -> None:
    """Show server.json or one dot-path value."""
    logger.debug("command config-file show config_path=%s path=%s", config_path, path)
    payload = _load_user_config_payload_or_fail(config_path)
    if path:
        try:
            payload = get_config_path(payload, path)
        except ConfigPathError as exc:
            raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


@config_file.command("validate")
@click.option("--config-path", type=click.Path(dir_okay=False, path_type=Path), default=None)
def validate_config_file(config_path: Path | None) -> None:
    """Validate user configuration from server.json."""
    logger.debug("command config-file validate config_path=%s", config_path)
    config = _load_user_config_or_fail(config_path)
    _validate_openviking_or_fail(config)
    click.echo("valid")


@config_file.command("set")
@click.option("--config-path", type=click.Path(dir_okay=False, path_type=Path), default=None)
@click.argument("path")
@click.argument("value")
def set_config_file_value(config_path: Path | None, path: str, value: str) -> None:
    """Set an existing server.json dot-path."""
    logger.debug("command config-file set config_path=%s path=%s", config_path, path)
    payload = _load_user_config_payload_or_fail(config_path)
    try:
        set_config_path(payload, path, parse_json_value(value))
    except ConfigPathError as exc:
        raise click.ClickException(str(exc)) from exc
    _save_user_config_payload_or_fail(payload, config_path)
    click.echo("updated")


@config_file.command("unset")
@click.option("--config-path", type=click.Path(dir_okay=False, path_type=Path), default=None)
@click.argument("path")
def unset_config_file_value(config_path: Path | None, path: str) -> None:
    """Unset an optional server.json dot-path or map key."""
    logger.debug("command config-file unset config_path=%s path=%s", config_path, path)
    payload = _load_user_config_payload_or_fail(config_path)
    try:
        unset_config_path(payload, path)
    except ConfigPathError as exc:
        raise click.ClickException(str(exc)) from exc
    _save_user_config_payload_or_fail(payload, config_path)
    click.echo("updated")


@main.group("server-config", hidden=True)
def server_config() -> None:
    """Manage user-level server configuration files."""


@server_config.command("paths")
def show_server_config_paths() -> None:
    """Print the user server configuration paths."""
    click.echo(
        json.dumps(
            {
                "config": str(get_server_config_path()),
                "lock": str(get_server_lock_path()),
                "release": str(get_release_lock_path()),
                "state": str(get_state_path()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@server_config.command("render")
@click.option(
    "--config-path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Path to server.json. Defaults to ~/.ov_mgn/server.json.",
)
def render_server_config(config_path: Path | None) -> None:
    """Render server.json as the expanded lock configuration."""
    logger.debug("command server-config render config_path=%s", config_path)
    config = load_user_server_config(config_path)
    _validate_openviking_or_fail(config)
    source = config_path or get_server_config_path()
    locked = render_locked_config(config, source=source)
    click.echo(json.dumps(locked.model_dump(mode="json"), ensure_ascii=False, indent=2))


@server_config.command("lock")
@click.option(
    "--config-path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Path to server.json. Defaults to ~/.ov_mgn/server.json.",
)
@click.option(
    "--lock-path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Path to server.json.lock. Defaults to ~/.ov_mgn/server.json.lock.",
)
def lock_server_config(config_path: Path | None, lock_path: Path | None) -> None:
    """Generate the read-only server.json.lock file."""
    logger.debug("command server-config lock config_path=%s lock_path=%s", config_path, lock_path)
    click.echo(str(_write_plan(config_path, lock_path)))


def _write_plan(config_path: Path | None, lock_path: Path | None) -> Path:
    logger.debug("write plan config_path=%s lock_path=%s", config_path, lock_path)
    config = load_user_server_config(config_path)
    _validate_openviking_or_fail(config)
    source = config_path or get_server_config_path()
    locked = render_locked_config(config, source=source)
    return write_lock_file(locked, lock_path)


def _to_json(config: AppConfig) -> str:
    return json.dumps(config.model_dump(mode="json"), ensure_ascii=False, indent=2)


def _load_user_config_or_fail(path: Path | None) -> UserServerConfig:
    try:
        return load_user_server_config(path)
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


def _load_user_config_payload_or_fail(path: Path | None) -> dict[str, Any]:
    try:
        return load_user_server_config(path).model_dump(mode="json", exclude_none=True)
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


def _save_user_config_payload_or_fail(payload: dict[str, Any], path: Path | None) -> None:
    try:
        save_user_server_config_data(payload, path)
    except (OSError, ValidationError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


def _validate_openviking_or_fail(config: UserServerConfig) -> None:
    try:
        validate_openviking_model_config(config)
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


def _resolve_log_level(
    configured: LogLevel,
    override: LogLevel | None,
    verbose: bool,
    quiet: bool,
) -> LogLevel:
    if verbose and quiet:
        raise click.UsageError("--verbose and --quiet cannot be used together.")
    if verbose:
        return "DEBUG"
    if quiet:
        return "WARNING"
    if override:
        return override.upper()
    return configured
