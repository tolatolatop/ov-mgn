import shutil

from ov_mgn.logging import get_logger
from ov_mgn.server_config import LockedServerConfig, LockedServiceSpec, ReleaseLock

logger = get_logger(__name__)


def prepare_release_data(
    *,
    service_name: str,
    service: LockedServiceSpec,
    locked: LockedServerConfig,
    release: ReleaseLock,
    state,
) -> bool:
    if service.branch and branch_needs_initial_data_copy(service_name, release, state):
        copy_branch_parent_data(
            service_name=service_name,
            service=service,
            locked=locked,
            release=release,
            state=state,
        )
        return True
    from ov_mgn.server_config import promote_data_dir

    promote_data_dir(service)
    return False


def branch_needs_initial_data_copy(
    service_name: str,
    release: ReleaseLock,
    state,
) -> bool:
    runtime = state.services.get(service_name)
    needs_copy = service_name not in release.services and (
        runtime is None
        or (runtime.candidate_release_id is None and runtime.online_release_id is None)
    )
    logger.debug(
        "branch initial data copy check service=%s needs_copy=%s",
        service_name,
        needs_copy,
    )
    return needs_copy


def copy_branch_parent_data(
    *,
    service_name: str,
    service: LockedServiceSpec,
    locked: LockedServerConfig,
    release: ReleaseLock,
    state,
) -> None:
    if service.branch is None:
        return
    parent_service_name = service.branch.parent_service
    parent_runtime = state.services.get(parent_service_name)
    logger.debug(
        "copying branch data service=%s parent_service=%s",
        service_name,
        parent_service_name,
    )
    if parent_runtime is None or not parent_runtime.online_release_id:
        raise ValueError(
            f"branch service {service_name} requires parent service "
            f"{parent_service_name} to have an online release"
        )
    parent_release_id = parent_runtime.online_release_id
    if parent_service_name not in release.services:
        raise ValueError(
            f"branch parent service {parent_service_name} is missing from release lock"
        )
    parent_service = release.services[parent_service_name]
    if parent_service.release_id != parent_release_id:
        raise ValueError(
            f"branch parent service {parent_service_name} release lock does not match "
            f"online release {parent_release_id}"
        )
    parent_data_dir = parent_service.release_data_dir
    if not parent_data_dir.exists() or not parent_data_dir.is_dir():
        raise ValueError(f"branch parent data directory does not exist: {parent_data_dir}")
    if service.release_data_dir.exists():
        raise ValueError(f"branch target data directory already exists: {service.release_data_dir}")
    logger.debug(
        "copying branch data source=%s target=%s parent_release_id=%s",
        parent_data_dir,
        service.release_data_dir,
        parent_release_id,
    )
    service.release_data_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(parent_data_dir, service.release_data_dir)
