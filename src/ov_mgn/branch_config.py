from datetime import UTC, datetime

from ov_mgn.server_config import BranchSpec, UserServerConfig


def add_branch_service_config(
    config: UserServerConfig,
    *,
    source_service: str,
    target_service: str,
    route_path: str | None = None,
    declared_at: datetime | None = None,
) -> UserServerConfig:
    if source_service not in config.services:
        raise ValueError(f"unknown source service: {source_service}")
    if target_service in config.services:
        raise ValueError(f"target service already exists: {target_service}")
    source = config.services[source_service]
    target_vars = {**source.openviking.vars, "profile": target_service}
    target_openviking = source.openviking.model_copy(update={"vars": target_vars})
    target = source.model_copy(
        update={
            "route_path": route_path or f"/{target_service}/",
            "openviking": target_openviking,
            "branch": BranchSpec(
                parent_service=source_service,
                declared_at=declared_at or datetime.now(UTC),
            ),
        }
    )
    return config.model_copy(update={"services": {**config.services, target_service: target}})
