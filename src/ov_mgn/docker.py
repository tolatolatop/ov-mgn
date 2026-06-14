import html
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ov_mgn.logging import get_logger
from ov_mgn.server_config import (
    OPENVIKING_CONFIG_FILE,
    LockedServerConfig,
    LockedServiceSpec,
    ReleaseLock,
    RuntimeState,
)

LABEL_SERVICE = "ov-mgn.service"
LABEL_ROLE = "ov-mgn.role"
LABEL_RELEASE_ID = "ov-mgn.release_id"
LABEL_STABLE_HOST = "ov-mgn.stable_host"
LABEL_STABLE_PORT = "ov-mgn.stable_port"
LABEL_GATEWAY = "ov-mgn.gateway"
GATEWAY_INFO_PATH = "/__ov-mgn/"
GATEWAY_INFO_JSON_PATH = "/__ov-mgn/services.json"

logger = get_logger(__name__)


@dataclass
class DockerClient:
    dry_run: bool = False

    def ensure_gateway_network(self, network_name: str) -> list[str]:
        command = ["docker", "network", "create", network_name]
        logger.debug("docker ensure network network=%s dry_run=%s", network_name, self.dry_run)
        self._run_allow_exists(command)
        return command

    def run_backend(self, service_name: str, service: LockedServiceSpec) -> list[str]:
        command = build_run_command(service_name, service, role="backend")
        logger.debug(
            "docker run backend service=%s container=%s network=%s image=%s dry_run=%s",
            service_name,
            service.release_container_name,
            service.network_name,
            service.image,
            self.dry_run,
        )
        self._run(command)
        return command

    def exec(
        self,
        container_name: str,
        command: list[str],
        *,
        capture_output: bool = False,
        allow_failure: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str] | None:
        docker_command = ["docker", "exec"]
        for key, value in sorted((env or {}).items()):
            docker_command.extend(["-e", f"{key}={value}"])
        logger.debug(
            "docker exec container=%s command=%s env_keys=%s dry_run=%s",
            container_name,
            command[:3],
            sorted((env or {}).keys()),
            self.dry_run,
        )
        return self._run(
            [*docker_command, container_name, *command],
            capture_output=capture_output,
            allow_failure=allow_failure,
        )

    def wait_healthy(self, container_name: str, *, timeout_seconds: int = 60) -> None:
        if self.dry_run:
            return
        logger.debug("docker wait healthy container=%s timeout=%s", container_name, timeout_seconds)
        deadline = time.monotonic() + timeout_seconds
        last_status = "unknown"
        while time.monotonic() < deadline:
            result = self._run(
                [
                    "docker",
                    "inspect",
                    "-f",
                    "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
                    container_name,
                ],
                capture_output=True,
                allow_failure=True,
            )
            if result and result.returncode == 0:
                last_status = result.stdout.strip()
                logger.debug(
                    "docker health status container=%s status=%s", container_name, last_status
                )
                if last_status in {"healthy", "running"}:
                    return
            time.sleep(2)
        raise TimeoutError(f"container did not become healthy: {container_name} ({last_status})")

    def settle(self, seconds: float) -> None:
        if not self.dry_run:
            time.sleep(seconds)

    def ensure_gateway(
        self,
        *,
        container_name: str,
        image: str,
        host: str,
        port: int,
        network_name: str,
        config_path: Path,
    ) -> list[str]:
        existing = self._run(
            ["docker", "ps", "-a", "-q", "--filter", f"name=^{container_name}$"],
            capture_output=True,
            allow_failure=True,
        )
        if existing and existing.stdout.strip():
            command = ["docker", "start", container_name]
            logger.debug(
                "docker start gateway container=%s dry_run=%s", container_name, self.dry_run
            )
            self._run(command, allow_failure=True)
            return command
        command = build_gateway_run_command(
            container_name=container_name,
            image=image,
            host=host,
            port=port,
            network_name=network_name,
            config_path=config_path,
        )
        logger.debug(
            "docker run gateway container=%s network=%s image=%s bind=%s:%s dry_run=%s",
            container_name,
            network_name,
            image,
            host,
            port,
            self.dry_run,
        )
        self._run(command)
        return command

    def reload_gateway(self, container_name: str) -> list[str]:
        command = ["docker", "exec", container_name, "nginx", "-s", "reload"]
        logger.debug("docker reload gateway container=%s dry_run=%s", container_name, self.dry_run)
        self._run(command)
        return command

    def stop_remove(self, container_name: str) -> None:
        logger.debug(
            "docker stop/remove check container=%s dry_run=%s", container_name, self.dry_run
        )
        existing = self._run(
            ["docker", "ps", "-a", "-q", "--filter", f"name=^{container_name}$"],
            capture_output=True,
            allow_failure=True,
        )
        if existing is None or not existing.stdout.strip():
            return
        logger.debug("docker remove container=%s dry_run=%s", container_name, self.dry_run)
        self._run(["docker", "rm", "-f", container_name], allow_failure=True)

    def inspect_status(self) -> str:
        command = [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"label={LABEL_SERVICE}",
            "--format",
            "{{.Names}}\t{{.Status}}\t{{.Ports}}",
        ]
        result = self._run(command, capture_output=True, allow_failure=True)
        logger.debug("docker inspect status complete dry_run=%s", self.dry_run)
        return "" if result is None else result.stdout

    def inspect_containers(self) -> list[dict[str, Any]]:
        command = [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"label={LABEL_SERVICE}",
            "--format",
            "\t".join(
                [
                    "{{.Names}}",
                    "{{.Status}}",
                    "{{.Ports}}",
                    f'{{{{.Label "{LABEL_SERVICE}"}}}}',
                    f'{{{{.Label "{LABEL_ROLE}"}}}}',
                    f'{{{{.Label "{LABEL_RELEASE_ID}"}}}}',
                    f'{{{{.Label "{LABEL_STABLE_HOST}"}}}}',
                    f'{{{{.Label "{LABEL_STABLE_PORT}"}}}}',
                ]
            ),
        ]
        result = self._run(command, capture_output=True, allow_failure=True)
        if result is None:
            return []
        containers = []
        for line in result.stdout.splitlines():
            fields = line.split("\t")
            if len(fields) != 8:
                continue
            name, status, ports, service, role, release_id, stable_host, stable_port = fields
            containers.append(
                {
                    "name": name,
                    "status": status,
                    "running": status.startswith("Up "),
                    "ports": ports,
                    "service": service,
                    "role": role,
                    "release_id": release_id,
                    "stable_host": stable_host,
                    "stable_port": _parse_int_or_string(stable_port),
                }
            )
        logger.debug("docker inspected containers count=%d", len(containers))
        return containers

    def inspect_gateway_container(
        self, container_name: str = "ov-mgn-gateway"
    ) -> dict[str, Any] | None:
        command = [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"name=^{container_name}$",
            "--format",
            "\t".join(["{{.Names}}", "{{.Status}}", "{{.Ports}}"]),
        ]
        result = self._run(command, capture_output=True, allow_failure=True)
        if result is None:
            return None
        for line in result.stdout.splitlines():
            fields = line.split("\t")
            if len(fields) != 3 or fields[0] != container_name:
                continue
            name, status, ports = fields
            payload = {
                "name": name,
                "status": status,
                "running": status.startswith("Up "),
                "ports": ports,
                "role": "gateway",
            }
            logger.debug(
                "docker inspected gateway container=%s running=%s", name, payload["running"]
            )
            return payload
        return None

    def container_running(self, container_name: str) -> bool:
        command = [
            "docker",
            "ps",
            "-q",
            "--filter",
            f"name=^{container_name}$",
            "--filter",
            "status=running",
        ]
        result = self._run(command, capture_output=True, allow_failure=True)
        running = result is None or bool(result.stdout.strip())
        logger.debug("docker container running container=%s running=%s", container_name, running)
        return running

    def _run_allow_exists(self, command: list[str]) -> None:
        result = self._run(command, capture_output=True, allow_failure=True)
        if result and result.returncode not in (0,):
            stderr = result.stderr.lower()
            if "already exists" not in stderr:
                result.check_returncode()

    def _run(
        self,
        command: list[str],
        *,
        capture_output: bool = False,
        allow_failure: bool = False,
    ) -> subprocess.CompletedProcess[str] | None:
        if self.dry_run:
            logger.debug("docker dry-run command=%s", _summarize_docker_command(command))
            return None
        logger.debug("docker run command=%s", _summarize_docker_command(command))
        result = subprocess.run(command, capture_output=capture_output, text=True)
        if not allow_failure:
            result.check_returncode()
        return result


def build_run_command(
    service_name: str,
    service: LockedServiceSpec,
    *,
    role: str,
) -> list[str]:
    if role != "backend":
        raise ValueError("only backend role is supported")

    container_name = service.release_container_name
    data_dir = service.release_data_dir

    command = [
        "docker",
        "run",
        "-d",
        "--name",
        container_name,
        "--network",
        service.network_name,
    ]
    command.extend(
        [
            "--label",
            f"{LABEL_SERVICE}={service_name}",
            "--label",
            f"{LABEL_ROLE}={role}",
            "--label",
            f"{LABEL_RELEASE_ID}={service.release_id}",
            "--label",
            f"{LABEL_STABLE_HOST}={service.stable_host}",
            "--label",
            f"{LABEL_STABLE_PORT}={service.stable_port}",
            "-v",
            f"{service.code_dir}:/app/code:ro",
            "-v",
            f"{service.config_dir}:/app/config:ro",
            "-v",
            f"{data_dir}:/app/data",
        ]
    )

    if service.secret_env_file:
        command.extend(["--env-file", str(service.secret_env_file)])

    command.extend(
        [
            "-e",
            f"OPENVIKING_CONFIG_FILE={OPENVIKING_CONFIG_FILE}",
            "-e",
            "OPENVIKING_CLI_CONFIG_FILE=/app/config/ovcli.conf",
            "-e",
            "PATH=/app/config/bin:/app/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        ]
    )

    for key, value in sorted(service.openviking.env.items()):
        command.extend(["-e", f"{key}={value}"])

    command.append(service.image)
    return command


def build_gateway_run_command(
    *,
    container_name: str,
    image: str,
    host: str,
    port: int,
    network_name: str,
    config_path: Path,
) -> list[str]:
    return [
        "docker",
        "run",
        "-d",
        "--name",
        container_name,
        "--network",
        network_name,
        "-p",
        f"{host}:{port}:80",
        "--label",
        f"{LABEL_GATEWAY}=true",
        "-v",
        f"{config_path}:/etc/nginx/conf.d/default.conf:ro",
        image,
    ]


def write_gateway_config(
    *,
    locked: LockedServerConfig,
    release: ReleaseLock,
    state: RuntimeState,
) -> Path:
    config_path = locked.gateway_config_path
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        render_gateway_config(locked=locked, release=release, state=state),
        encoding="utf-8",
    )
    return config_path


def render_gateway_config(
    *,
    locked: LockedServerConfig,
    release: ReleaseLock,
    state: RuntimeState,
) -> str:
    locations: list[str] = [_gateway_info_locations(locked=locked, release=release, state=state)]
    for service_name in sorted(locked.services):
        service = locked.services[service_name]
        runtime = state.services.get(service_name)
        if not runtime:
            continue
        if runtime.online_release_id:
            active = _service_for_release(
                service_name=service_name,
                release_id=runtime.online_release_id,
                locked=locked,
                release=release,
            )
            locations.append(
                _location(service.route_path, active.release_container_name, active.backend_port)
            )
        if runtime.candidate_release_id:
            candidate = _service_for_release(
                service_name=service_name,
                release_id=runtime.candidate_release_id,
                locked=locked,
                release=release,
            )
            locations.append(
                _location(
                    f"{service.route_path}__candidate/",
                    candidate.release_container_name,
                    candidate.backend_port,
                )
            )
        elif runtime.online_release_id:
            locations.append(f"    location {service.route_path}__candidate/ {{ return 404; }}\n")
    body = "\n".join(locations) if locations else "    location / { return 404; }\n"
    return f"server {{\n    listen 80;\n    server_name _;\n{body}}}\n"


def render_gateway_info(
    *,
    locked: LockedServerConfig,
    release: ReleaseLock,
    state: RuntimeState,
) -> dict[str, Any]:
    base_url = f"http://{locked.gateway.host}:{locked.gateway.port}"
    services = []
    for service_name in sorted(locked.services):
        service = locked.services[service_name]
        runtime = state.services.get(service_name)
        online_release_id = runtime.online_release_id if runtime else None
        candidate_release_id = runtime.candidate_release_id if runtime else None
        stage = _gateway_service_stage(
            locked=service,
            released=release.services.get(service_name),
            online_release_id=online_release_id,
            candidate_release_id=candidate_release_id,
        )
        services.append(
            {
                "name": service_name,
                "status": stage,
                "route_path": service.route_path,
                "entry_url": f"{base_url}{service.route_path}",
                "candidate_url": f"{base_url}{service.route_path}__candidate/",
                "active_release_id": online_release_id,
                "candidate_release_id": candidate_release_id,
                "enabled": service.enabled,
            }
        )
    return {
        "gateway": {
            "host": locked.gateway.host,
            "port": locked.gateway.port,
            "info_url": f"{base_url}{GATEWAY_INFO_PATH}",
            "services_json_url": f"{base_url}{GATEWAY_INFO_JSON_PATH}",
        },
        "services": services,
    }


def _gateway_info_locations(
    *,
    locked: LockedServerConfig,
    release: ReleaseLock,
    state: RuntimeState,
) -> str:
    info = render_gateway_info(locked=locked, release=release, state=state)
    json_body = json.dumps(info, ensure_ascii=False, separators=(",", ":"))
    html_body = _render_gateway_info_html(info)
    return (
        f"    location = {GATEWAY_INFO_PATH[:-1]} {{ return 302 {GATEWAY_INFO_PATH}; }}\n"
        f"    location = {GATEWAY_INFO_JSON_PATH} {{\n"
        "        default_type application/json;\n"
        f"        return 200 '{_nginx_return_escape(json_body)}';\n"
        "    }\n"
        f"    location = {GATEWAY_INFO_PATH} {{\n"
        "        default_type text/html;\n"
        f"        return 200 '{_nginx_return_escape(html_body)}';\n"
        "    }\n"
    )


def _render_gateway_info_html(info: dict[str, Any]) -> str:
    rows = []
    for service in info["services"]:
        entry_url = html.escape(service["entry_url"])
        candidate_url = html.escape(service["candidate_url"])
        rows.append(
            "<tr>"
            f"<td>{html.escape(service['name'])}</td>"
            f"<td>{html.escape(service['status'])}</td>"
            f'<td><a href="{entry_url}">{entry_url}</a></td>'
            f'<td><a href="{candidate_url}">{candidate_url}</a></td>'
            f"<td>{html.escape(service['active_release_id'] or '')}</td>"
            f"<td>{html.escape(service['candidate_release_id'] or '')}</td>"
            "</tr>"
        )
    services_json_url = html.escape(info["gateway"]["services_json_url"])
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>ov-mgn services</title>"
        "<style>"
        "body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;margin:24px;color:#17202a}"
        "table{border-collapse:collapse;width:100%;max-width:1200px}"
        "th,td{border-bottom:1px solid #d8dee4;padding:10px;text-align:left;vertical-align:top}"
        "th{background:#f6f8fa;font-weight:600}"
        "a{color:#0969da}"
        ".meta{margin:0 0 16px;color:#57606a}"
        "</style></head><body>"
        "<h1>ov-mgn services</h1>"
        f'<p class="meta">JSON: <a href="{services_json_url}">{services_json_url}</a></p>'
        "<table><thead><tr><th>Service</th><th>Status</th><th>Entry</th>"
        "<th>Candidate</th><th>Active release</th><th>Candidate release</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
        "</body></html>"
    )


def _gateway_service_stage(
    *,
    locked: LockedServiceSpec,
    released: LockedServiceSpec | None,
    online_release_id: str | None,
    candidate_release_id: str | None,
) -> str:
    if not locked.enabled:
        return "disabled"
    if candidate_release_id and online_release_id:
        return "candidate_pending_promotion"
    if candidate_release_id:
        return "candidate"
    if online_release_id:
        if released and released.release_id == online_release_id:
            return "online"
        return "inconsistent"
    return "planned"


def _nginx_return_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")


def _location(path: str, container_name: str, backend_port: int) -> str:
    return (
        f"    location {path} {{\n"
        f"        proxy_pass http://{container_name}:{backend_port}/;\n"
        "        proxy_set_header Host $host;\n"
        "        proxy_set_header X-Real-IP $remote_addr;\n"
        "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
        "        proxy_set_header X-Forwarded-Proto $scheme;\n"
        "    }\n"
    )


def _service_for_release(
    *,
    service_name: str,
    release_id: str,
    locked: LockedServerConfig,
    release: ReleaseLock,
) -> LockedServiceSpec:
    if service_name in release.services and release.services[service_name].release_id == release_id:
        return release.services[service_name]
    service = locked.services[service_name]
    if service.release_id == release_id:
        return service
    return service.model_copy(
        update={
            "release_id": release_id,
            "release_container_name": f"ov-mgn-{service_name}-{release_id}",
            "code_dir": service.code_dir.parents[1] / release_id / "code",
            "config_dir": service.config_dir.parents[1] / release_id / "config",
            "release_data_dir": service.release_data_dir.parents[1] / release_id / "data",
            "source": service.source.model_copy(
                update={"snapshot_path": service.code_dir.parents[1] / release_id / "code"}
            ),
            "openviking": service.openviking.model_copy(
                update={
                    "vars": {**service.openviking.vars, "release_id": release_id},
                    "config_file": service.config_dir.parents[1]
                    / release_id
                    / "config"
                    / "openviking.conf",
                    "cli_config_file": service.config_dir.parents[1]
                    / release_id
                    / "config"
                    / "ovcli.conf",
                }
            ),
        }
    )


def _parse_int_or_string(value: str) -> int | str:
    try:
        return int(value)
    except ValueError:
        return value


def _summarize_docker_command(command: list[str]) -> str:
    if len(command) < 2:
        return " ".join(command)
    if command[:3] == ["docker", "network", "create"] and len(command) >= 4:
        return f"docker network create {command[3]}"
    if command[:2] == ["docker", "run"]:
        name = _option_value(command, "--name") or "<unnamed>"
        network = _option_value(command, "--network") or "<default>"
        image = command[-1] if command else "<unknown>"
        return f"docker run name={name} network={network} image={image}"
    if command[:2] == ["docker", "exec"]:
        container_index = 2
        index = 2
        while index < len(command) and command[index] == "-e":
            index += 2
            container_index = index
        container = command[container_index] if container_index < len(command) else "<unknown>"
        subcommand = command[container_index + 1 : container_index + 4]
        return f"docker exec container={container} command={' '.join(subcommand)}"
    if command[:2] == ["docker", "rm"] and command[-1:]:
        return f"docker rm {command[-1]}"
    if command[:2] == ["docker", "start"] and command[-1:]:
        return f"docker start {command[-1]}"
    if command[:2] == ["docker", "ps"]:
        return "docker ps"
    if command[:2] == ["docker", "inspect"]:
        return f"docker inspect {command[-1]}"
    return " ".join(command[:3])


def _option_value(command: list[str], option: str) -> str | None:
    try:
        index = command.index(option)
    except ValueError:
        return None
    value_index = index + 1
    if value_index >= len(command):
        return None
    return command[value_index]
