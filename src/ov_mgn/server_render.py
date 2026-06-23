import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from ov_mgn.logging import get_logger
from ov_mgn.openviking import (
    render_managed_openviking_config_with_key,
    render_openviking_cli_wrapper,
)
from ov_mgn.server_config import (
    LockedOpenViking,
    LockedServerConfig,
    LockedServiceSpec,
    LockedSource,
    ServerDefaults,
    ServiceSpec,
    SourceSpec,
    UserServerConfig,
    get_server_config_path,
)

logger = get_logger(__name__)


def render_locked_config(
    config: UserServerConfig,
    source: Path | None = None,
    generated_at: datetime | None = None,
    used_ports: set[int] | None = None,
) -> LockedServerConfig:
    generated_at = generated_at or datetime.now(UTC)
    source = source or get_server_config_path()
    allocated_ports = set(used_ports or set())
    services: dict[str, LockedServiceSpec] = {}
    logger.debug(
        "rendering locked config source=%s generated_at=%s services=%d used_ports=%s",
        source,
        generated_at.isoformat(),
        len(config.services),
        sorted(allocated_ports),
    )

    for name, spec in sorted(config.services.items()):
        service = _render_locked_service(name, config.defaults, spec, generated_at, allocated_ports)
        logger.debug(
            "rendered service name=%s release_id=%s candidate_port=%d source_type=%s",
            name,
            service.release_id,
            service.candidate_port,
            service.source.type,
        )
        services[name] = service
        allocated_ports.add(service.candidate_port)

    return LockedServerConfig(
        version=config.version,
        generated_at=generated_at,
        source=source,
        gateway=config.defaults.gateway,
        gateway_config_path=config.defaults.data_root.expanduser() / "gateway" / "nginx.conf",
        gateway_container_name="ov-mgn-gateway",
        services=services,
    )


def materialize_service(service: LockedServiceSpec, *, root_api_key: str | None = None) -> None:
    logger.debug(
        "materializing service release_id=%s code_dir=%s config_dir=%s candidate_data_dir=%s "
        "release_data_dir=%s",
        service.release_id,
        service.code_dir,
        service.config_dir,
        service.candidate_data_dir,
        service.release_data_dir,
    )
    service.code_dir.mkdir(parents=True, exist_ok=True)
    service.config_dir.mkdir(parents=True, exist_ok=True)
    service.candidate_data_dir.mkdir(parents=True, exist_ok=True)

    if service.source.type == "local" and service.source.original_path:
        logger.debug(
            "copying local source original_path=%s code_dir=%s",
            service.source.original_path.expanduser(),
            service.code_dir,
        )
        if service.code_dir.exists():
            shutil.rmtree(service.code_dir)
        shutil.copytree(service.source.original_path.expanduser(), service.code_dir)

    render_managed_openviking_config_with_key(service, root_api_key=root_api_key)
    render_openviking_cli_wrapper(service)


def promote_data_dir(service: LockedServiceSpec) -> None:
    logger.debug(
        "promoting candidate data release_id=%s candidate_data_dir=%s release_data_dir=%s",
        service.release_id,
        service.candidate_data_dir,
        service.release_data_dir,
    )
    service.release_data_dir.parent.mkdir(parents=True, exist_ok=True)
    if service.release_data_dir.exists():
        return
    if service.candidate_data_dir.exists():
        shutil.move(str(service.candidate_data_dir), str(service.release_data_dir))
    else:
        service.release_data_dir.mkdir(parents=True, exist_ok=True)


def _render_locked_service(
    name: str,
    defaults: ServerDefaults,
    spec: ServiceSpec,
    generated_at: datetime,
    used_ports: set[int],
) -> LockedServiceSpec:
    short_sha = _source_short_sha(spec.source)
    release_id = f"{name}-{generated_at.strftime('%Y%m%dT%H%M%S')}-{short_sha}"
    data_root = defaults.data_root.expanduser()
    release_root = data_root / name / "releases" / release_id
    candidate_port = _allocate_port(defaults.port_range, used_ports)
    source = _lock_source(spec.source, release_root / "code")
    variables = {"profile": name, "service": name, "release_id": release_id, **spec.openviking.vars}

    return LockedServiceSpec(
        enabled=spec.enabled,
        image=spec.image or defaults.image,
        backend_port=defaults.backend_port,
        stable_host=spec.stable_host,
        stable_port=spec.stable_port,
        candidate_host=spec.stable_host,
        candidate_port=candidate_port,
        route_path=spec.route_path or f"/{name}/",
        release_id=release_id,
        source=source,
        network_name=defaults.gateway.network_name,
        candidate_container_name=f"ov-mgn-{name}-candidate-{release_id}",
        online_container_name=f"ov-mgn-{name}-online",
        release_container_name=f"ov-mgn-{name}-{release_id}",
        code_dir=release_root / "code",
        config_dir=release_root / "config",
        candidate_data_dir=data_root / name / "candidates" / release_id / "data",
        release_data_dir=release_root / "data",
        secret_env_file=defaults.secret_env_file.expanduser() if defaults.secret_env_file else None,
        openviking=LockedOpenViking(
            vars=variables,
            env=spec.openviking.env,
            template_path=spec.openviking.template_path,
            model_config_file=defaults.openviking.model_config_file.expanduser(),
            config_file=release_root / "config" / "openviking.conf",
            cli_config_file=release_root / "config" / "ovcli.conf",
        ),
        branch=spec.branch,
    )


def _source_short_sha(source: SourceSpec) -> str:
    if source.type == "git":
        commit = _resolve_git_commit(source)
        return commit[:7]
    if source.path:
        return _resolve_local_sha(source.path)[:7]
    return "unknown"


def _lock_source(source: SourceSpec, code_dir: Path) -> LockedSource:
    if source.type == "git":
        return LockedSource(
            type="git",
            repo=source.repo,
            ref=source.ref,
            commit_sha=_resolve_git_commit(source),
        )
    return LockedSource(
        type="local",
        original_path=source.path.expanduser() if source.path else None,
        snapshot_path=code_dir,
    )


def _resolve_git_commit(source: SourceSpec) -> str:
    repo = source.repo or ""
    ref = source.ref or "HEAD"
    repo_path = Path(repo).expanduser()
    if repo_path.exists():
        command = ["git", "-C", str(repo_path), "rev-parse", ref]
    else:
        command = ["git", "ls-remote", repo, ref]
    logger.debug("resolving git source repo=%s ref=%s local=%s", repo, ref, repo_path.exists())
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    output = result.stdout.strip()
    if not output:
        return "unknown"
    return output.split()[0]


def _resolve_local_sha(path: Path) -> str:
    repo_path = path.expanduser()
    logger.debug("resolving local source sha path=%s", repo_path)
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "local"
    return result.stdout.strip() or "local"


def _allocate_port(port_range: tuple[int, int], used_ports: set[int]) -> int:
    start, end = port_range
    logger.debug(
        "allocating candidate port range=%d-%d used_ports=%s", start, end, sorted(used_ports)
    )
    for port in range(start, end + 1):
        if port not in used_ports:
            return port
    raise ValueError(f"no free port in range {start}-{end}")
