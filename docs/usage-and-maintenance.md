# ov-mgn 使用和维护手册

`ov-mgn` 用 candidate/promote 流程管理 OpenViking 服务：先生成候选 release，
通过 candidate 路由检查，再手动 promote 到稳定路由。用户只维护两类配置：

- `server.json`：服务托管配置，例如镜像、端口、源码、网关和附加环境变量。
- `model.json`：模型相关敏感配置，例如 embedding、VLM、bot agents。

`ov-mgn` 会自动生成完整的 `/app/config/openviking.conf`，包括 `server`、`storage`
和 release-local `root_api_key`。不要再维护整份 OpenViking 模板。

## 1. 快速启动

### 1.1 准备环境

```bash
uv sync --dev
uv run ov-mgn --help
docker ps
```

准备服务源码目录，或在 `server.json` 里改成 Git source：

```bash
mkdir -p ./openviking-alpha
mkdir -p ~/.ov_mgn
chmod 700 ~/.ov_mgn
```

### 1.2 用向导生成初始配置

推荐先用交互式向导生成 `server.json` 和 `model.json`：

```bash
uv run ov-mgn wizard init
```

向导默认写入 `~/.ov_mgn/server.json` 和 `~/.ov_mgn/model.json`。如果文件已存在，默认
跳过；`server.json` 可选择追加普通服务，`model.json` 只有二次确认后才会重写。API
key 可以留空，向导会写入 `replace-me` 占位值，之后再编辑成真实值。所有向导写入前
都会再次询问是否确认写入；选择 no 不会修改文件。

`wizard init` 的主流程只覆盖首次可运行所需的基础项：是否创建首个服务、服务源码位置、
以及需要哪些模型能力。route path、服务级镜像、Git ref、gateway、backend 端口等不常
改的内容放在高级确认或 `wizard edit` 里。源码目录、数据目录、模型配置文件和 secret
env 文件等路径输入支持 Tab 自动补全。

后续追加普通服务可以使用：

```bash
uv run ov-mgn wizard add-service
```

已有配置的小改动可以使用交互式编辑脚手架：

```bash
uv run ov-mgn wizard edit
```

`wizard edit` 会先选择 `server` 或 `model`。编辑 `server.json` 时继续分成
`defaults` 和 `service`：默认配置默认只询问默认镜像、数据目录和可选 secret env；
只有确认需要高级选项后，才会继续询问模型配置文件、backend 端口、gateway 绑定、网络
和 candidate 端口范围。服务配置默认只询问服务名和源码位置；只有确认需要高级服务选项
后，才会继续询问 Git ref、route path 和服务级镜像。编辑已有服务时，默认值来自当前
配置，未进入高级选项则保留已有高级字段。写入前仍复用现有 schema 校验。编辑
`model.json` 时先选择 `embedding`、`vlm` 或 `bot`，再按 API URL、API key、模型名的
顺序填写；默认值来自当前配置。每次修改后可以选择继续修改，返回 `server` / `model`
选择列表；结束修改后再统一确认写入已改过的配置文件。

分支服务不要用 `wizard add-service` 手写，继续使用 `branch SOURCE TARGET`。

### 1.3 手动准备 model.json

`~/.ov_mgn/model.json` 只允许 `embedding`、`vlm`、`bot` 顶层字段。真实 API key
只写在这里，不写入 `server.json`、lock 或 status 输出。

```bash
cat > ~/.ov_mgn/model.json <<'EOF'
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
EOF
chmod 600 ~/.ov_mgn/model.json
```

### 1.4 手动准备 server.json

```bash
cat > ~/.ov_mgn/server.json <<'EOF'
{
  "version": 1,
  "defaults": {
    "image": "ghcr.io/volcengine/openviking:latest",
    "backend_port": 1933,
    "data_root": "~/.ov_mgn/data",
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
EOF
chmod 600 ~/.ov_mgn/server.json
```

### 1.5 发布服务

```bash
uv run ov-mgn config-file validate
uv run ov-mgn plan
uv run ov-mgn up alpha
```

检查 candidate：

```bash
uv run ov-mgn status --no-docker
docker ps --filter label=ov-mgn.service=alpha
```

candidate 入口：

```text
http://127.0.0.1:18080/alpha/__candidate/
```

确认后切到线上：

```bash
uv run ov-mgn promote alpha
uv run ov-mgn status
```

稳定入口：

```text
http://127.0.0.1:18080/alpha/
```

服务目录：

```text
http://127.0.0.1:18080/__ov-mgn/
http://127.0.0.1:18080/__ov-mgn/services.json
```

## 2. 日常变更

### 2.1 修改 model.json 后生效

已经运行的容器不会热加载 `model.json`。修改模型配置后，必须创建并切换到新 release：

```bash
uv run ov-mgn config-file validate
uv run ov-mgn plan
uv run ov-mgn up alpha
```

先检查 candidate：

```text
http://127.0.0.1:18080/alpha/__candidate/
```

确认模型配置可用后 promote：

```bash
uv run ov-mgn promote alpha
uv run ov-mgn status
```

这会为新 release 重新渲染 `openviking.conf`。新的 `model.json` 内容只进入 release
配置目录，不进入 `server.json.lock` 或 `status` 输出。

### 2.2 修改 server.json 字段

`config-file set` 会先按 JSON 解析值，解析失败则作为普通字符串：

```bash
uv run ov-mgn config-file set defaults.gateway.port 18081
uv run ov-mgn config-file set defaults.image ghcr.io/volcengine/openviking:latest
uv run ov-mgn config-file set services.alpha.route_path /alpha/
uv run ov-mgn config-file set services.alpha.openviking.env.TZ Asia/Shanghai
uv run ov-mgn config-file set services.alpha.openviking.vars.profile alpha-v2
uv run ov-mgn config-file unset services.alpha.image
```

修改后走同一套发布流程：

```bash
uv run ov-mgn config-file validate
uv run ov-mgn plan
uv run ov-mgn up alpha
uv run ov-mgn promote alpha
```

`set` 不会创建不存在的中间结构。例如 `services.beta.route_path` 中的
`services.beta` 不存在时会失败。它可以在已有 service 的 `openviking.env` 或
`openviking.vars` 字典中新增键。

### 2.3 更新源码

local source：

```bash
cd ./openviking-alpha
git pull
cd -

uv run ov-mgn plan
uv run ov-mgn up alpha
uv run ov-mgn promote alpha
```

Git source：

```bash
uv run ov-mgn config-file set services.alpha.source.ref main
uv run ov-mgn config-file validate
uv run ov-mgn plan
uv run ov-mgn up alpha
uv run ov-mgn promote alpha
```

`up` 会把本地源码复制进 release code 目录。复制完成后，后续对原始源码目录的修改
不会影响已经启动的 release。

### 2.4 创建分支服务

`branch` 命令只编辑 `server.json`，用于声明一个新的服务从已有服务分支而来。它不复制
数据、不启动容器、不修改 lock/state/release。真实数据复制发生在目标服务第一次
`up` 时，复制源服务当时的 online release data。

```bash
uv run ov-mgn branch alpha alpha-exp --route-path /alpha-exp/
```

该命令会复制 `alpha` 的服务配置到 `alpha-exp`，并写入：

```json
{
  "branch": {
    "parent_service": "alpha",
    "declared_at": "2026-06-15T..."
  }
}
```

确认 `server.json` 后，可以提交到配置仓库。运维服务器按普通发布流程创建分支服务：

```bash
uv run ov-mgn config-file validate
uv run ov-mgn plan
uv run ov-mgn up alpha-exp
```

检查 candidate：

```text
http://127.0.0.1:18080/alpha-exp/__candidate/
```

确认后上线：

```bash
uv run ov-mgn promote alpha-exp
```

上线后 `alpha` 和 `alpha-exp` 是完全独立的 OpenViking 服务。要对哪个分支做增量解析，
就进入哪个服务的容器执行原生 `ov add-resource`。

### 2.5 停止服务

```bash
uv run ov-mgn down alpha
```

`down` 会停止已知 backend 容器并移除网关路由；不会删除 `server.json`、源码、
release 数据目录或 lock 文件。

### 2.6 回滚或切换旧 release

如果旧 backend 容器仍在运行，可以切回旧 release：

```bash
uv run ov-mgn switch alpha alpha-20260613T120000-abcd123
uv run ov-mgn status
```

`switch` 会确认目标 backend 容器仍在运行，然后把稳定路径切到该 release。

## 3. 配置说明

字段级参考单独维护在 [配置字段参考](configuration-reference.md)。该文档按
`server.json` 和 `model.json` 分别列出：

- 最小必填配置。
- 常用字段。
- 高级字段。
- `必须提供`、`默认值`、`可自动推理`、`高级配置`、`不建议手写` 等标签。

日常只需要按本文的快速启动示例维护配置；需要确认字段默认值、校验约束或高级配置时，
再查阅字段参考。

### 3.1 Secret 文件

如果服务还需要额外环境变量文件：

```bash
cat > ~/.ov_mgn/secrets.env <<'EOF'
OPENVIKING_TOKEN=replace-me
EOF
chmod 600 ~/.ov_mgn/secrets.env

uv run ov-mgn config-file set defaults.secret_env_file ~/.ov_mgn/secrets.env
```

secret env 文件内容不会写入 lock 或 status 输出。受 `ov-mgn` 管理的变量会被过滤，
不能通过 secret env 覆盖。

清空 secret env 文件路径：

```bash
uv run ov-mgn config-file set defaults.secret_env_file null
uv run ov-mgn config-file validate
```

## 4. 高级选项

### 4.1 网关模型

backend 容器不绑定 host 端口，而是加入全局 gateway network。Nginx 容器绑定唯一入口
端口并按路径转发：

- 稳定入口：`http://{gateway_host}:{gateway_port}/{service}/`
- candidate 入口：`http://{gateway_host}:{gateway_port}/{service}/__candidate/`
- 服务目录：`http://{gateway_host}:{gateway_port}/__ov-mgn/`
- 服务目录 JSON：`http://{gateway_host}:{gateway_port}/__ov-mgn/services.json`

Nginx 转发时会去掉服务前缀，`/alpha/foo` 到后端会变成 `/foo`。
`/__ov-mgn/` 是保留路径，不能作为服务 `route_path` 前缀。

### 4.2 容器内 ov CLI

OpenViking 镜像内置 `ov` CLI。`ov-mgn` 会在 release 配置目录生成
`/app/config/bin/ov` wrapper，并把 `/app/config/bin` 放在容器 `PATH` 最前面。

常见检查命令：

```bash
container=ov-mgn-alpha-alpha-20260614T120000-abcd123

docker exec "$container" ov health
docker exec "$container" ov ls viking:// -l 256 -n 256
docker exec "$container" ov chat -m "你好"
```

wrapper 会用 `server.root_api_key` 刷新本次命令使用的 `default/default` 普通用户 key，
再设置 `OPENVIKING_CLI_CONFIG_FILE` 和 `VIKINGBOT_API_KEY` 后调用镜像自带的
`/app/.venv/bin/ov`。

不要并发执行多个容器内 `ov` 命令。wrapper 会刷新同一个 `default/default` 用户 key；
并发命令可能出现一个命令刚拿到的 key 被另一个命令刷新失效，表现为
`Invalid API Key`。

### 4.3 导入源码作为 resources

使用容器内路径 `/app/code`：

```bash
container=ov-mgn-alpha-alpha-20260614T120000-abcd123

docker exec "$container" ov add-resource /app/code \
  --to viking://resources/alpha-code \
  --ignore-dirs '.git,node_modules,dist,build,.venv,__pycache__' \
  --wait \
  --timeout 300
```

检查导入结果：

```bash
docker exec "$container" ov stat viking://resources/alpha-code -o json
docker exec "$container" ov tree viking://resources/alpha-code -L 2
docker exec "$container" ov ls viking://resources/alpha-code -l 128 -n 128
```

如果源码目录包含不需要处理的媒体文件，可以排除：

```bash
docker exec "$container" ov add-resource /app/code \
  --to viking://resources/alpha-code-text \
  --ignore-dirs '.git,node_modules,dist,build,.venv,__pycache__' \
  --exclude '*.gif,*.png,*.jpg,*.jpeg,*.webp,*.svg' \
  --wait \
  --timeout 300
```

`--to` 目标必须不存在。需要重导时先确认目标内容，再显式删除：

```bash
docker exec "$container" ov rm viking://resources/alpha-code
```

## 5. 状态、文件和审计

### 5.1 查看状态

```bash
uv run ov-mgn status
uv run ov-mgn status --no-docker
```

日常优先看 `services_summary.<service>`：

- `stage`：当前阶段，例如 `configured`、`planned`、`candidate`、
  `candidate_pending_promotion`、`online`、`disabled`、`orphaned`、`inconsistent`。
- `ok`：`true` 表示当前记录一致，`false` 表示存在不一致，`null` 表示跳过了
  Docker 检查。
- `issues`：需要处理的问题列表。
- `candidate_release_id` / `online_release_id`：当前候选和线上 release。
- `gateway`：稳定入口、candidate 入口、route path 和 Nginx 路由检查结果。

查看用户配置：

```bash
uv run ov-mgn config-file show
uv run ov-mgn config-file show services.alpha.openviking.env
```

查看内部路径：

```bash
uv run ov-mgn server-config paths
```

### 5.2 文件职责

默认文件位于 `~/.ov_mgn/`：

- `server.json`：用户维护的服务配置。
- `model.json`：用户维护的模型敏感配置。
- `server.json.lock`：`plan` 生成的 candidate 部署计划，只读。
- `release.json.lock`：`promote` 写入的线上 release 记录，只读。
- `state.json.lock`：运行时状态，记录 candidate 和 online release id。
- `secrets.env`：可选 Docker secret env 文件。

不要手动编辑 `server.json.lock`、`release.json.lock` 或 `state.json.lock`，除非是在故障
恢复场景下明确知道影响。

### 5.3 命令读写层级

| 命令 | 读取 | 写入 |
| --- | --- | --- |
| `wizard init` | `server.json`、`model.json` | `server.json`、`model.json` |
| `wizard add-service` | `server.json` | `server.json` |
| `wizard edit` | `server.json` 或 `model.json` | `server.json` 或 `model.json` |
| `config-file show/set/unset` | `server.json` | `server.json` |
| `config-file validate` | `server.json`、`model.json` | 无 |
| `branch SOURCE TARGET` | `server.json` | `server.json` |
| `plan` | `server.json`、`model.json` | `server.json.lock` |
| `up SERVICE` | `server.json.lock`、`model.json`、`release.json.lock`、`state.json.lock` | release 配置目录、backend 容器、gateway 配置、`state.json.lock` |
| `promote SERVICE` | `server.json.lock`、`release.json.lock`、`state.json.lock` | gateway 路由、`release.json.lock`、`state.json.lock` |
| `switch SERVICE RELEASE_ID` | `server.json.lock`、`release.json.lock`、`state.json.lock`、Docker | gateway 路由、`release.json.lock`、`state.json.lock` |
| `down SERVICE` | `server.json.lock`、`release.json.lock`、`state.json.lock` | 删除已知容器、`state.json.lock` |
| `status` | `server.json`、`server.json.lock`、`release.json.lock`、`state.json.lock`、Docker | 无 |

## 6. Q&A 和故障处理

### Q: 修改 model.json 后为什么线上没变化？

`model.json` 不会热加载到已运行容器。执行：

```bash
uv run ov-mgn config-file validate
uv run ov-mgn plan
uv run ov-mgn up alpha
uv run ov-mgn promote alpha
```

`up` 会为新 release 重新生成 `openviking.conf`；`promote` 才会把稳定路由切过去。

### Q: branch 后为什么还没有复制数据？

`branch` 只声明配置，方便把 `server.json` 提交到配置仓库。真实复制发生在目标服务第一
次 `up` 时：

```bash
uv run ov-mgn plan
uv run ov-mgn up alpha-exp
```

`up alpha-exp` 会读取 `alpha-exp.branch.parent_service`，找到父服务当前 online release，
并在线复制它的 `data` 目录作为目标服务的初始数据。父服务必须已经 online；否则
`up` 会失败。

### Q: 分支后源服务和目标服务还会同步吗？

不会。分支关系只用于目标服务首次 `up` 时复制初始数据。之后源服务和目标服务完全
独立。要对某个分支做增量解析，就进入该分支服务的容器执行原生 OpenViking 命令，
例如：

```bash
docker exec -it <alpha-exp-container> ov add-resource /app/code --to viking://resources/alpha-exp-code
```

### Q: `config-file validate` 失败怎么办？

先看错误路径。常见原因：

- `defaults.gateway.enabled` 被设为 `false`。
- 两个服务使用了相同的 `route_path`。
- local source 路径不存在或不是目录。
- `defaults.openviking.model_config_file` 不存在、不是 JSON、缺少模型段，或包含
  `embedding`、`vlm`、`bot` 之外的顶层字段。
- env key 不符合 `[A-Z_][A-Z0-9_]*`，或试图覆盖 `ov-mgn` 管理的环境变量。
- 旧配置仍包含 `services.<name>.openviking.template_path`。

### Q: candidate 启动了但不想上线怎么办？

查看状态：

```bash
uv run ov-mgn status
docker ps --filter label=ov-mgn.service=alpha
```

放弃 candidate：

```bash
uv run ov-mgn down alpha
uv run ov-mgn plan
uv run ov-mgn up alpha
```

### Q: promote 失败后能不能手动删目录？

先不要。先查看：

```bash
uv run ov-mgn status --no-docker
docker ps --filter label=ov-mgn.service=alpha
```

确认 backend、gateway、release lock 和 state 的状态后，再决定是重新 `promote`、
`down`，还是人工介入 Docker。

### Q: 容器内 ov 报 Invalid API Key 怎么查？

先确认执行的是 wrapper：

```bash
docker exec "$container" sh -c 'which ov; head -n 5 "$(which ov)"'
```

正常应输出 `/app/config/bin/ov`。如果输出 `/app/.venv/bin/ov`，说明容器不是由当前
版本 `ov-mgn` 创建，或 `PATH` 没有包含 `/app/config/bin`。重新 `plan`、`up` 并
`promote` 一个新 release。

再确认配置里存在 root key，但不要打印真实 key：

```bash
docker exec "$container" ov health
docker exec "$container" sh -c 'python - <<PY
import json
conf = json.load(open("/app/config/openviking.conf"))
print(bool(conf.get("server", {}).get("root_api_key")))
PY'
```

### Q: `ov add-resource --wait` 或 `ov wait` 返回 HTTP request failed 怎么办？

先确认资源是否已经写入：

```bash
docker exec "$container" ov health
docker exec "$container" ov stat viking://resources/alpha-code -o json
docker exec "$container" ov tree viking://resources/alpha-code -L 2
docker exec "$container" ov status -o json
```

如果 `stat` 成功且 `tree` 能列出目录，说明资源已经写入；后续问题多半在后台摘要、
语义节点或媒体解析队列。查看日志定位具体文件类型或上游模型错误：

```bash
docker logs --tail 200 "$container"
```
