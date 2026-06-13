import subprocess
from dataclasses import dataclass

from ov_mgn.server_config import LockedServiceSpec

LABEL_SERVICE = "ov-mgn.service"
LABEL_ROLE = "ov-mgn.role"
LABEL_RELEASE_ID = "ov-mgn.release_id"
LABEL_STABLE_HOST = "ov-mgn.stable_host"
LABEL_STABLE_PORT = "ov-mgn.stable_port"


@dataclass
class DockerClient:
    dry_run: bool = False

    def ensure_network(self, service: LockedServiceSpec) -> list[str]:
        command = ["docker", "network", "create", service.network_name]
        self._run_allow_exists(command)
        return command

    def run_candidate(self, service_name: str, service: LockedServiceSpec) -> list[str]:
        command = build_run_command(service_name, service, role="candidate")
        self._run(command)
        return command

    def run_online(self, service_name: str, service: LockedServiceSpec) -> list[str]:
        command = build_run_command(service_name, service, role="online")
        self._run(command)
        return command

    def stop_remove(self, container_name: str) -> None:
        self._run(["docker", "rm", "-f", container_name], allow_failure=True)

    def remove_online_conflicts(self, stable_host: str, stable_port: int) -> list[str]:
        containers = self.find_online_by_stable_port(stable_host, stable_port)
        for container in containers:
            self.stop_remove(container)
        return containers

    def find_online_by_stable_port(self, stable_host: str, stable_port: int) -> list[str]:
        command = [
            "docker",
            "ps",
            "-a",
            "-q",
            "--filter",
            f"label={LABEL_ROLE}=online",
            "--filter",
            f"label={LABEL_STABLE_HOST}={stable_host}",
            "--filter",
            f"label={LABEL_STABLE_PORT}={stable_port}",
        ]
        result = self._run(command, capture_output=True)
        if result is None:
            return []
        return [line for line in result.stdout.splitlines() if line]

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
        return "" if result is None else result.stdout

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
            return None
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
    if role not in {"candidate", "online"}:
        raise ValueError("role must be candidate or online")

    host_port = service.candidate_port if role == "candidate" else service.stable_port
    host = service.candidate_host if role == "candidate" else service.stable_host
    container_name = (
        service.candidate_container_name if role == "candidate" else service.online_container_name
    )
    data_dir = service.candidate_data_dir if role == "candidate" else service.release_data_dir

    command = [
        "docker",
        "run",
        "-d",
        "--name",
        container_name,
        "--network",
        service.network_name,
        "-p",
        f"{host}:{host_port}:8080",
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

    if service.secret_env_file:
        command.extend(["--env-file", str(service.secret_env_file)])

    for key, value in sorted(service.openviking.env.items()):
        command.extend(["-e", f"{key}={value}"])

    command.append(service.image)
    return command
