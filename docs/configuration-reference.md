# ov-mgn 配置字段参考

用户只需要维护 `server.json` 和 `model.json`。`server.json.lock`、
`release.json.lock`、`state.json.lock`、release 目录下的 `openviking.conf` 和
`ovcli.conf` 都是运行时产物，不属于用户配置面。

可以用 `ov-mgn wizard init` 交互生成初始 `server.json` / `model.json`，也可以按本文
手写。已有文件默认不会被覆盖；`model.json` 重写前会再次确认。API key 提示允许留空，
留空时向导写入 `replace-me` 占位值。

字段标签含义：

- **必须提供**：没有该字段就无法通过 ov-mgn 校验，或无法生成有用服务。
- **默认值**：可以省略；省略时 ov-mgn 使用表格中的默认值。
- **可自动推理**：可以省略；ov-mgn 会根据服务名、时间、源码版本或运行状态生成。
- **高级配置**：日常不需要；只有多服务、网络、分支或特殊部署需求时再改。
- **不建议手写**：字段仍被 schema 接受，但当前流程已弃用或应由命令生成。

## 1. server.json 最小配置

`server.json` 最小有用配置只需要声明一个服务和它的源码。其余字段都有默认值或可自动
推理。

local source 最小配置：

```json
{
  "services": {
    "alpha": {
      "source": {
        "type": "local",
        "path": "./openviking-alpha"
      }
    }
  }
}
```

Git source 最小配置：

```json
{
  "services": {
    "alpha": {
      "source": {
        "type": "git",
        "repo": "https://example.com/openviking.git",
        "ref": "main"
      }
    }
  }
}
```

`ref` 可以省略；省略时按 `HEAD` 解析。`local.path` 必须指向已存在的目录。

## 2. server.json 常用字段

| 字段 | 标签 | 默认值/自动推理 | 说明 |
| --- | --- | --- | --- |
| `version` | 默认值 | `1` | 用户配置版本。当前只使用 `1`。 |
| `defaults.image` | 默认值 | `ghcr.io/volcengine/openviking:latest` | 所有服务默认使用的 OpenViking 镜像。服务级 `image` 可覆盖。 |
| `defaults.data_root` | 默认值 | `~/.ov_mgn/data` | release、candidate data、网关配置的根目录。 |
| `defaults.openviking.model_config_file` | 默认值 | `~/.ov_mgn/model.json` | `model.json` 路径。只要配置了服务，`config-file validate` 和 `plan` 都会校验该文件。 |
| `defaults.gateway.host` | 默认值 | `127.0.0.1` | Nginx gateway 绑定 host。 |
| `defaults.gateway.port` | 默认值 | `18080` | Nginx gateway 绑定端口。 |
| `services.<name>` | 必须提供 | 无 | 服务名是 map key，必须匹配 `[A-Za-z][A-Za-z0-9_-]*`。 |
| `services.<name>.source.type` | 必须提供 | 无 | 源码类型，只能是 `local` 或 `git`。 |
| `services.<name>.source.path` | local 必须提供 | 无 | `type=local` 时必须提供，路径必须存在且是目录。 |
| `services.<name>.source.repo` | git 必须提供 | 无 | `type=git` 时必须提供，支持本地 Git 仓库路径或远端仓库 URL。 |
| `services.<name>.source.ref` | 默认值 | `HEAD` | `type=git` 时用于解析 commit；可写 `main`、tag、branch 或 commit。 |
| `services.<name>.route_path` | 可自动推理 | `/{service}/` | gateway 稳定路由。必须以 `/` 开始和结束；不能包含 `/__candidate/`，不能使用 `/__ov-mgn/` 前缀。 |
| `services.<name>.image` | 高级配置、默认值 | 继承 `defaults.image` | 单个服务覆盖镜像。`wizard add-service` 会询问该字段，留空则省略。 |
| `services.<name>.openviking.env` | 默认值 | `{}` | 附加容器环境变量。key 必须匹配 `[A-Z_][A-Z0-9_]*`。 |
| `services.<name>.openviking.vars` | 默认值 | `{}` | 服务变量。ov-mgn 会自动加入 `profile`、`service`、`release_id`，用户值可覆盖同名变量。常用来设置 `profile`。 |

常用完整示例：

```json
{
  "version": 1,
  "defaults": {
    "image": "ghcr.io/volcengine/openviking:latest",
    "data_root": "~/.ov_mgn/data",
    "openviking": {
      "model_config_file": "~/.ov_mgn/model.json"
    },
    "gateway": {
      "host": "127.0.0.1",
      "port": 18080
    }
  },
  "services": {
    "alpha": {
      "route_path": "/alpha/",
      "source": {
        "type": "local",
        "path": "./openviking-alpha"
      },
      "openviking": {
        "env": {
          "TZ": "Asia/Shanghai"
        },
        "vars": {
          "profile": "alpha-prod"
        }
      }
    }
  }
}
```

## 3. server.json 高级字段

| 字段 | 标签 | 默认值/自动推理 | 说明 |
| --- | --- | --- | --- |
| `defaults.port_range` | 高级配置、默认值 | `[30000, 39999]` | candidate 后端端口分配范围。当前 backend 通过 Docker network 被 gateway 访问，不直接绑定 host 端口；该字段仍写入 lock/state 供状态和兼容逻辑使用。 |
| `defaults.backend_port` | 高级配置、默认值 | `1933` | 容器内 OpenViking HTTP 端口。只有镜像内服务端口变更时才改。 |
| `defaults.secret_env_file` | 高级配置、默认值 | `null` | Docker `--env-file` 路径。内容不会写入 lock/status；受 ov-mgn 管理的 env key 会被过滤。 |
| `defaults.gateway.enabled` | 默认值、必须为 true | `true` | 当前只支持 gateway 模式；设为 `false` 会校验失败。 |
| `defaults.gateway.image` | 高级配置、默认值 | `nginx:stable-alpine` | gateway 容器镜像。 |
| `defaults.gateway.network_name` | 高级配置、默认值 | `ov-mgn-gateway` | backend 和 gateway 共享的 Docker network。 |
| `services.<name>.enabled` | 默认值 | `true` | 设为 `false` 后服务仍可出现在配置中，但不能 `up`。 |
| `services.<name>.stable_host` | 高级配置、默认值 | `127.0.0.1` | 运行状态和 Docker label 使用的稳定 host。gateway 模式入口以 `defaults.gateway.host` 为准。 |
| `services.<name>.stable_port` | 高级配置、默认值 | `0` | 运行状态和 Docker label 使用的稳定端口。gateway 模式入口以 `defaults.gateway.port` 为准。 |
| `services.<name>.branch.parent_service` | 高级配置、必须提供 | 无 | 分支服务的父服务。必须是同一 `server.json` 中已经存在的服务，且不能等于自己。推荐用 `ov-mgn branch SOURCE TARGET` 生成。 |
| `services.<name>.branch.declared_at` | 高级配置、可自动推理 | `branch` 命令写入当前时间 | 手写 branch 时必须提供 ISO datetime；用 CLI 创建时自动写入。 |
| `services.<name>.openviking.template_path` | 不建议手写 | `null` | 已弃用。只要服务设置了该字段，OpenViking 配置校验会失败。请使用 `defaults.openviking.model_config_file`。 |

## 4. model.json 最小配置

`model.json` 是 OpenViking 模型配置片段。ov-mgn 只做外层约束：

- 文件必须存在且是 JSON object。
- 顶层只允许 `embedding`、`vlm`、`bot`。
- 至少提供其中一个顶层段。
- 段内字段不由 ov-mgn 逐项校验，会原样合并进生成的 `openviking.conf`。

最小可通过 ov-mgn 校验的 `model.json`：

```json
{
  "embedding": {
    "dense": {
      "provider": "openai",
      "api_key": "replace-me",
      "model": "text-embedding-3-small"
    }
  }
}
```

这个最小样例只表示通过 ov-mgn 的文件校验；具体 provider 是否还需要 `api_base`、
`dimension` 或其他字段，以 OpenViking 和模型供应商要求为准。

向导生成或编辑模型段时按 API URL、API key、模型名的顺序采集；provider 默认写入
`openai`，如果已有配置中存在其他 provider 则会保留。生成结果仍只是 OpenViking 模型
配置片段。复杂 provider 字段可以生成后继续手动编辑。

## 5. model.json 常用字段

| 字段 | 标签 | 默认值/自动推理 | 说明 |
| --- | --- | --- | --- |
| `embedding` | 至少一个顶层段必须提供 | 无 | embedding 配置段。常用于 dense embedding。 |
| `embedding.dense.provider` | 常用配置 | 无 | 模型供应商，例如 `openai`。段内字段由 OpenViking 使用。 |
| `embedding.dense.api_base` | 常用配置 | 无 | 供应商 API base。是否必填取决于 provider 和运行环境。 |
| `embedding.dense.api_key` | 常用配置、敏感字段 | 无 | embedding API key。只写在 `model.json` 或安全 secret 中，不写入 `server.json`。 |
| `embedding.dense.model` | 常用配置 | 无 | embedding 模型名。 |
| `embedding.dense.dimension` | 常用配置 | 无 | embedding 维度。是否必填取决于模型和 OpenViking 配置。 |
| `vlm` | 至少一个顶层段必须提供 | 无 | 视觉/语言模型配置段。 |
| `vlm.provider` | 常用配置 | 无 | VLM/LLM provider。 |
| `vlm.api_base` | 常用配置 | 无 | VLM/LLM API base。 |
| `vlm.api_key` | 常用配置、敏感字段 | 无 | VLM/LLM API key。 |
| `vlm.model` | 常用配置 | 无 | VLM/LLM 模型名。 |
| `bot` | 至少一个顶层段必须提供 | 无 | bot/agent 配置段。 |
| `bot.agents` | 常用配置 | 无 | agent 使用的模型配置。常见字段与 `vlm` 类似。 |
| `bot.agents.api_key` | 常用配置、敏感字段 | 无 | agent API key。 |
| `bot.agents.model` | 常用配置 | 无 | agent 模型名。 |

常用完整示例：

```json
{
  "embedding": {
    "max_concurrent": 2,
    "max_retries": 5,
    "dense": {
      "provider": "openai",
      "api_base": "https://example.invalid/v1",
      "api_key": "replace-me",
      "model": "text-embedding-3-small",
      "dimension": 1536
    }
  },
  "vlm": {
    "provider": "openai",
    "api_base": "https://example.invalid/v1",
    "api_key": "replace-me",
    "model": "gpt-5.1-chat",
    "temperature": 0.7
  },
  "bot": {
    "agents": {
      "provider": "openai",
      "api_base": "https://example.invalid/v1",
      "api_key": "replace-me",
      "model": "gpt-5.1-chat",
      "max_tool_iterations": 50,
      "memory_window": 50
    }
  }
}
```

## 6. model.json 高级说明

| 项目 | 标签 | 说明 |
| --- | --- | --- |
| 顶层 `server` | 不允许 | ov-mgn 会自动生成 `server.host`、`server.port`、`server.root_api_key`；`model.json` 里写 `server` 会校验失败。 |
| 顶层 `storage` | 不允许 | ov-mgn 会自动生成 local storage 配置；`model.json` 里写 `storage` 会校验失败。 |
| 顶层自定义段 | 不允许 | 顶层只能是 `embedding`、`vlm`、`bot`。 |
| 段内扩展字段 | 高级配置 | ov-mgn 不限制 `embedding`、`vlm`、`bot` 内部字段；会原样交给 OpenViking。 |
| API key | 敏感字段 | 不会进入 `server.json.lock` 或 `status` 输出；会写入 release 目录下生成的 `openviking.conf`。确保 `model.json` 和 release 配置目录权限正确。 |
| 向导占位 API key | 常用配置 | 如果交互输入 API key 时留空，写入 `replace-me`，用于提醒后续替换真实密钥。 |

最终生成的 `/app/config/openviking.conf` 结构可以理解为：

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 1933,
    "root_api_key": "<ov-mgn generated>"
  },
  "storage": {
    "workspace": "/app/data",
    "vectordb": {"name": "context", "backend": "local"},
    "agfs": {"backend": "local"}
  },
  "embedding": {},
  "vlm": {},
  "bot": {}
}
```

其中 `embedding`、`vlm`、`bot` 来自 `model.json`；`server` 和 `storage` 由 ov-mgn
生成，用户不要手写。

## 7. 受 ov-mgn 管理的配置

不要在 `openviking.env` 里设置这些变量：

- `OPENVIKING_CONFIG_FILE`
- `OPENVIKING_CLI_CONFIG_FILE`
- `VIKINGBOT_API_KEY`
- `PATH`

`ov-mgn` 会自动注入：

- `OPENVIKING_CONFIG_FILE=/app/config/openviking.conf`
- `OPENVIKING_CLI_CONFIG_FILE=/app/config/ovcli.conf`
- `PATH=/app/config/bin:...`

`server.root_api_key` 由 `ov-mgn` 自动生成并保存在 release 配置目录，不写入
`server.json`。

`wizard add-service` 只添加普通服务。需要从已有服务派生数据和配置时，使用
`ov-mgn branch SOURCE_SERVICE TARGET_SERVICE` 生成 `branch` 字段。
