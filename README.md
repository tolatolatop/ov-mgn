# ov-mgn

`ov-mgn` is a lightweight OpenViking service manager. It manages isolated
OpenViking knowledge-base service copies with a candidate/promote workflow:
new releases start on temporary ports, then a user promotes a checked candidate
onto the stable service port.

## Commands

```bash
uv sync --dev
uv run ov-mgn --help
uv run ov-mgn plan
uv run ov-mgn up alpha
uv run ov-mgn promote alpha
uv run ov-mgn status
uv run pytest
uv run ruff check .
```

For a step-by-step operator workflow covering new service setup, first deploy,
updates, promotion, and maintenance, see
[docs/usage-and-maintenance.md](docs/usage-and-maintenance.md).

The main lifecycle commands are:

- `plan`: read `~/.ov_mgn/server.json` and write read-only `server.json.lock`.
- `up SERVICE`: materialize the locked release and start its candidate container.
- `promote SERVICE`: move candidate data into the release, remove only online
  containers with the same stable host/port, and start the stable online
  container.
- `down SERVICE`: stop known candidate and online containers for the service.
- `status`: print lock, release, runtime state, and Docker status.

## Configuration Management

`server.json` is the user configuration entry point. `release.json.lock` is the
record of promoted online configuration. `server.json.lock` is still generated
by `plan`, `up`, and `promote`, but it is mainly a candidate deployment artifact
instead of a user-facing configuration surface.

Use `config-file` for minimal `server.json` inspection and field edits:

```bash
uv run ov-mgn config-file show
uv run ov-mgn config-file show services.alpha.stable_port
uv run ov-mgn config-file validate
uv run ov-mgn config-file set defaults.image example/openviking:test
uv run ov-mgn config-file set defaults.port_range '[31000,31999]'
uv run ov-mgn config-file set services.alpha.stable_port 18080
uv run ov-mgn config-file set services.alpha.openviking.env.TZ Asia/Shanghai
uv run ov-mgn config-file unset services.alpha.openviking.vars.profile
```

`set` parses values as JSON first and falls back to plain strings, so values
such as `18080`, `true`, `null`, `[31000,31999]`, and `"quoted string"` are
accepted. It does not create missing intermediate structures, so it cannot be
used to create a service. It can update existing service fields and add keys
inside existing `openviking.env` or `openviking.vars` maps.

`unset` can remove optional fields such as `services.alpha.image` and mapping
keys such as `services.alpha.openviking.env.TZ`. It refuses to remove whole
services or required fields such as `services.alpha.stable_port`.

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
- `state.json`: generated mutable runtime state.

## server.json

```json
{
  "defaults": {
    "port_range": [30000, 39999],
    "image": "openviking/openviking:latest",
    "data_root": "~/.ov_mgn/data",
    "secret_env_file": "~/.ov_mgn/secrets.env"
  },
  "services": {
    "alpha": {
      "stable_host": "127.0.0.1",
      "stable_port": 18080,
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

Secrets are passed to Docker with `--env-file`; secret values are not read into
the lock file.

## Docker Model

`ov-mgn` uses `docker run` directly in v1.

- Network: `ov-mgn-{service}`
- Candidate container: `ov-mgn-{service}-candidate-{release_id}`
- Online container: `ov-mgn-{service}-online`
- Code: `~/.ov_mgn/data/{service}/releases/{release_id}/code`
- Config: `~/.ov_mgn/data/{service}/releases/{release_id}/config`
- Candidate data: `~/.ov_mgn/data/{service}/candidates/{release_id}/data`
- Release data: `~/.ov_mgn/data/{service}/releases/{release_id}/data`

Managed containers include `ov-mgn.service`, `ov-mgn.role`,
`ov-mgn.release_id`, `ov-mgn.stable_host`, and `ov-mgn.stable_port` labels.
