# ov-mgn

`ov-mgn` is a lightweight OpenViking service manager. It manages isolated
OpenViking knowledge-base service copies with a candidate/promote workflow:
new releases start as backend containers behind a single Nginx gateway, then a
user promotes a checked candidate route onto the stable service route.

## Commands

```bash
uv sync --dev
uv run ov-mgn --help
uv run ov-mgn plan
uv run ov-mgn up alpha
uv run ov-mgn promote alpha
uv run ov-mgn branch alpha alpha-exp --route-path /alpha-exp/
uv run ov-mgn switch alpha alpha-20260613T120000-abcd123
uv run ov-mgn status
uv run pytest
uv run ruff check .
```

For a step-by-step operator workflow covering new service setup, first deploy,
updates, promotion, and maintenance, see
[docs/usage-and-maintenance.md](docs/usage-and-maintenance.md).

The main lifecycle commands are:

- `plan`: read `~/.ov_mgn/server.json` and write read-only `server.json.lock`.
- `up SERVICE`: materialize the locked release, start its backend container, and
  expose it at the candidate preview route.
- `promote SERVICE`: point the stable gateway route at the candidate backend and
  record the promoted release.
- `branch SOURCE_SERVICE TARGET_SERVICE`: add a new service config branch in
  `server.json`; the first `up TARGET_SERVICE` copies data from the source
  service's current online release.
- `switch SERVICE RELEASE_ID`: point the stable route at an already running
  backend release.
- `down SERVICE`: remove the service routes and stop known backend containers.
- `status`: print lock, release, runtime state, and Docker status.

## Configuration Management

`server.json` is the user configuration entry point. `release.json.lock` is the
record of promoted online configuration. `server.json.lock` is still generated
by `plan`, `up`, and `promote`, but it is mainly a candidate deployment artifact
instead of a user-facing configuration surface.

Use `config-file` for minimal `server.json` inspection and field edits:

```bash
uv run ov-mgn config-file show
uv run ov-mgn config-file show defaults.gateway.port
uv run ov-mgn config-file validate
uv run ov-mgn config-file set defaults.image example/openviking:test
uv run ov-mgn config-file set defaults.port_range '[31000,31999]'
uv run ov-mgn config-file set defaults.gateway.port 18080
uv run ov-mgn config-file set services.alpha.route_path /alpha/
uv run ov-mgn config-file set services.alpha.openviking.env.TZ Asia/Shanghai
uv run ov-mgn config-file unset services.alpha.openviking.vars.profile
```

`set` parses values as JSON first and falls back to plain strings, so values
such as `18080`, `true`, `null`, `[31000,31999]`, and `"quoted string"` are
accepted. It does not create missing intermediate structures, so it cannot be
used to create a service. It can update existing service fields and add keys
inside existing `openviking.env` or `openviking.vars` maps.

`unset` can remove optional fields such as `services.alpha.image`,
`services.alpha.route_path`, and mapping keys such as
`services.alpha.openviking.env.TZ`. It refuses to remove whole services or
required fields such as `services.alpha.source`.

Validation runs before and after edits. Failed changes return a non-zero exit
code and leave the existing file unchanged:

```bash
$ uv run ov-mgn config-file set services.alpha.openviking.env.bad-key value
Error: 1 validation error for UserServerConfig
services.alpha.openviking.env
  Value error, env keys must match [A-Z_][A-Z0-9_]*
```

Git sources only require a non-empty `repo`; `ref` defaults to `HEAD` during
locking. Local sources must point to an existing directory. Template paths must
point to an existing file. Secret env file paths are recorded as paths only;
secret file contents are not read into validate, show, lock, or status output.

## Files

`ov-mgn` stores user-level state under `~/.ov_mgn/`:

- `server.json`: user-edited service configuration.
- `server.json.lock`: generated candidate deployment plan, written read-only.
- `release.json.lock`: generated record of promoted releases, written read-only.
- `state.json.lock`: generated mutable runtime state.

## server.json

```json
{
  "defaults": {
    "port_range": [30000, 39999],
    "image": "ghcr.io/volcengine/openviking:latest",
    "backend_port": 1933,
    "data_root": "~/.ov_mgn/data",
    "secret_env_file": null,
    "openviking": {
      "model_config_file": "~/.ov_mgn/model.json"
    },
    "gateway": {
      "enabled": true,
      "host": "127.0.0.1",
      "port": 18080,
      "image": "nginx:stable-alpine",
      "network_name": "ov-mgn-gateway"
    }
  },
  "services": {
    "alpha": {
      "stable_host": "127.0.0.1",
      "stable_port": 0,
      "route_path": "/alpha/",
      "source": {
        "type": "git",
        "repo": "https://example.com/openviking.git",
        "ref": "main"
      },
      "openviking": {
        "vars": {
          "profile": "alpha"
        },
        "env": {
          "TZ": "Asia/Shanghai"
        }
      }
    }
  }
}
```

Sources can be Git repositories or local directories. Git sources are locked to
a commit SHA when possible. Local sources are copied into the release code
directory when `up` materializes the candidate, so later edits to the original
directory do not change the release.

Set `secret_env_file` to a real env file path only when the service needs one.
Secrets are passed to Docker with `--env-file`; secret values are not read into
the lock file. `backend_port` is the port exposed by the service inside the
container; current OpenViking images listen on `1933`.

Real OpenViking deployments should provide `defaults.openviking.model_config_file`
pointing at a private JSON file, normally `~/.ov_mgn/model.json`. That file may
only contain `embedding`, `vlm`, and `bot` top-level sections. `ov-mgn`
generates the rest of `/app/config/openviking.conf`, including
`server.host=0.0.0.0`, `server.port=defaults.backend_port`,
`storage.workspace=/app/data`, and a release-local `server.root_api_key`.
`OPENVIKING_CONFIG_FILE=/app/config/openviking.conf` is injected into the
container automatically; do not set it in `openviking.env`.

Gateway mode is the only supported deployment model. `defaults.gateway.enabled`
must remain `true`; setting it to `false` is rejected. The external service URL
is `http://{defaults.gateway.host}:{defaults.gateway.port}{route_path}`.
`stable_host` and `stable_port` are retained as compatibility metadata and do
not define the public entry point.

## Docker Model

`ov-mgn` uses `docker run` directly in v1.

Clients use one stable gateway entry point:

- Stable URL: `http://{gateway_host}:{gateway_port}/{service}/`
- Candidate preview URL: `http://{gateway_host}:{gateway_port}/{service}/__candidate/`
- Gateway service directory: `http://{gateway_host}:{gateway_port}/__ov-mgn/`
- Gateway service directory JSON:
  `http://{gateway_host}:{gateway_port}/__ov-mgn/services.json`
- Backend container: `ov-mgn-{service}-{release_id}`
- Network: `defaults.gateway.network_name`
- Gateway container: `ov-mgn-gateway`
- Code: `~/.ov_mgn/data/{service}/releases/{release_id}/code`
- Config: `~/.ov_mgn/data/{service}/releases/{release_id}/config`
- Release data: `~/.ov_mgn/data/{service}/releases/{release_id}/data`

Managed backend containers include `ov-mgn.service`, `ov-mgn.role=backend`,
`ov-mgn.release_id`, `ov-mgn.stable_host`, and `ov-mgn.stable_port` labels.

Gateway backend containers do not bind host ports. Nginx strips the service
route prefix before proxying, so `/alpha/foo` reaches the backend as `/foo`.
Nginx proxies to each backend container's configured `backend_port`.
The built-in `__ov-mgn` paths are reserved and cannot be used as service
`route_path` values. They are regenerated on every `up`, `promote`, `switch`,
and `down` gateway reload.
The first gateway release model supports candidate preview plus manual
`promote`/`switch`; it does not do percentage-weighted traffic splitting.

Backend containers also include a release-local `ov` wrapper at
`/app/config/bin/ov`. It is placed before the image's native CLI in `PATH` and
refreshes a temporary `default/default` user key for ordinary `ov` commands, so
commands such as `docker exec <container> ov ls ...` and
`docker exec <container> ov chat ...` do not require operators to maintain a
static user API key. Run container-side `ov` commands serially; concurrent
commands can invalidate each other's temporary key.

To import a deployed release's source into OpenViking resources, run
`ov add-resource /app/code --to viking://resources/{service}-code` inside the
backend container and verify with `ov stat` or `ov tree`. See the maintenance
manual for the full command, ignore/exclude options, and wait-queue caveats.
