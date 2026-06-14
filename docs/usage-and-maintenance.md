# ov-mgn 使用和维护手册

本文按实际使用顺序说明如何用 `ov-mgn` 完成 OpenViking 服务的新建、部署、
更新、切换和日常维护。

## 1. 准备环境

安装依赖并确认命令可用：

```bash
uv sync --dev
uv run ov-mgn --help
```

确认 Docker 可用：

```bash
docker ps
```

准备服务源码目录或 Git 仓库。例如本地源码目录：

```bash
mkdir -p ./openviking-alpha
```

如果服务需要密钥，准备 secret env 文件。`ov-mgn` 只把该文件路径传给 Docker，
不会把 secret 内容写入 lock 或 status 输出：

```bash
mkdir -p ~/.ov_mgn
chmod 700 ~/.ov_mgn
cat > ~/.ov_mgn/secrets.env <<'EOF'
OPENVIKING_TOKEN=replace-me
EOF
chmod 600 ~/.ov_mgn/secrets.env
```

## 2. 新建服务配置

当前 `config-file` 只负责查看、校验和编辑已有配置字段，不负责创建服务。
首次新建服务时，先手写 `~/.ov_mgn/server.json`：

```bash
mkdir -p ~/.ov_mgn
chmod 700 ~/.ov_mgn
cat > ~/.ov_mgn/server.json <<'EOF'
{
  "defaults": {
    "port_range": [31000, 31999],
    "image": "openviking/openviking:latest",
    "backend_port": 1933,
    "data_root": "~/.ov_mgn/data",
    "secret_env_file": null,
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

关键字段：

- `defaults.port_range`：为每次发布计划保留的内部 candidate 端口范围。gateway-only
  模式下该端口不作为外部入口。
- `defaults.image`：默认 OpenViking 镜像。
- `defaults.backend_port`：容器内 OpenViking HTTP 服务端口。当前 OpenViking
  镜像默认监听 `1933`；旧镜像如果监听 `8080`，需要显式设为 `8080`。
- `defaults.data_root`：release、candidate 数据和配置目录根路径。
- `defaults.secret_env_file`：Docker `--env-file` 路径；默认 `null`，只有确实需要
  容器环境变量文件时才设置为实际路径。
- `defaults.gateway`：单机 Nginx 网关配置。gateway 是唯一支持的部署模式，
  `enabled` 必须为 `true`；客户端只访问 `host:port` 这一个入口。
- `services.alpha.stable_host` / `stable_port`：兼容字段，不决定外部访问地址。
- `services.alpha.route_path`：服务路径，默认 `/{service}/`。
- `services.alpha.source`：源码来源，支持 `local` 或 `git`。
- `services.alpha.openviking.env`：传给容器的环境变量。
- `services.alpha.openviking.vars`：渲染 OpenViking 配置模板时使用的变量。

校验并查看配置：

```bash
uv run ov-mgn config-file validate
uv run ov-mgn config-file show
uv run ov-mgn config-file show defaults.gateway.port
```

成功时 `validate` 输出：

```text
valid
```

## 3. 首次部署

生成候选部署计划：

```bash
uv run ov-mgn plan
```

该命令读取 `~/.ov_mgn/server.json`，生成只读的
`~/.ov_mgn/server.json.lock`。lock 文件冻结本次 candidate 发布所需的信息，
包括 release id、容器名、route path、代码目录、配置目录和数据目录。

启动 candidate：

```bash
uv run ov-mgn up alpha
```

`up alpha` 会：

- 读取 `server.json.lock` 中的 `alpha` 服务计划。
- 复制 local source 到 release code 目录，或锁定 Git source。
- 渲染 OpenViking 配置文件。
- 创建 release data 目录。
- 启动 backend release 容器。
- 更新 Nginx 配置，让 candidate 预览路由指向该 backend。
- 更新 `state.json.lock` 中的 candidate release id。

业务容器不绑定 host 端口，而是加入全局 gateway network；Nginx 容器绑定唯一入口
端口并按路径转发：

- 稳定入口是 `http://127.0.0.1:18080/alpha/`。
- candidate 预览入口是 `http://127.0.0.1:18080/alpha/__candidate/`。
- 网关内置服务目录页面是 `http://127.0.0.1:18080/__ov-mgn/`。
- 网关内置服务目录 JSON 是 `http://127.0.0.1:18080/__ov-mgn/services.json`。
- Nginx 转发时去掉服务前缀，`/alpha/foo` 到后端会变成 `/foo`。
- Nginx 会转发到 `defaults.backend_port`，不要只修改 OpenViking 配置里的监听端口
  而忘记同步这个字段。
- `__ov-mgn` 是保留路径，不能作为任何服务的 `route_path` 前缀。
- `up alpha` 启动 backend release 容器并更新 candidate 预览路由。
- `promote alpha` 不重启 backend，只把稳定路由切到 candidate release。
- 第一版灰度只支持 candidate 预览加手动 `promote` / `switch`，不做百分比权重分流。

检查 candidate。检查方式取决于服务自身，例如：

```bash
uv run ov-mgn status --no-docker
docker ps --filter label=ov-mgn.service=alpha
```

如果检查通过，将 candidate 切换为线上版本：

```bash
uv run ov-mgn promote alpha
```

`promote alpha` 不停止或重启 candidate backend，不迁移数据目录；它只写入
`release.json.lock`，更新 `state.json.lock`，把稳定路由切到 candidate release，
并 reload gateway。promote 后 candidate 预览路由返回 404，直到下一次 `up`。

确认线上状态：

```bash
uv run ov-mgn status
```

首次部署的最短命令链是：

```bash
uv run ov-mgn config-file validate
uv run ov-mgn plan
uv run ov-mgn up alpha
uv run ov-mgn promote alpha
uv run ov-mgn status
```

如果要快速回滚或切到仍在运行的旧 release：

```bash
uv run ov-mgn switch alpha alpha-20260613T120000-abcd123
uv run ov-mgn status
```

`switch` 会确认目标 backend 容器仍在运行，然后把稳定路径切到该 release。

## 4. 更新配置并切换

配置更新使用 `config-file set` 和 `config-file unset`。`set` 会先按 JSON 解析值，
解析失败则作为普通字符串。

常见修改：

```bash
uv run ov-mgn config-file set defaults.image openviking/openviking:v2
uv run ov-mgn config-file set defaults.port_range '[32000,32999]'
uv run ov-mgn config-file set services.alpha.enabled true
uv run ov-mgn config-file set services.alpha.openviking.env.TZ Asia/Shanghai
uv run ov-mgn config-file set services.alpha.openviking.vars.profile alpha-v2
uv run ov-mgn config-file unset services.alpha.image
```

`set` 不会创建不存在的中间结构。例如 `services.beta.route_path` 中的
`services.beta` 不存在时会失败。它可以在已有 service 的
`openviking.env` 或 `openviking.vars` 字典中新增键。

修改后重新校验、生成新计划、启动 candidate、检查并切换：

```bash
uv run ov-mgn config-file validate
uv run ov-mgn plan
uv run ov-mgn up alpha

# 检查 candidate 通过后
uv run ov-mgn promote alpha
uv run ov-mgn status
```

## 5. 更新源码并切换

### 本地源码

如果 `source.type` 是 `local`，先更新本地源码目录：

```bash
cd ./openviking-alpha
git pull
cd -
```

然后重新生成计划并启动新的 candidate：

```bash
uv run ov-mgn plan
uv run ov-mgn up alpha

# 检查 candidate 通过后
uv run ov-mgn promote alpha
```

`up` 会把本地源码复制进 release code 目录。复制完成后，后续对原始源码目录的
修改不会影响已经启动的 release。

### Git 源码

如果 `source.type` 是 `git`，可以修改 `ref`：

```bash
uv run ov-mgn config-file set services.alpha.source.ref main
uv run ov-mgn config-file validate
uv run ov-mgn plan
uv run ov-mgn up alpha

# 检查 candidate 通过后
uv run ov-mgn promote alpha
```

`plan` 会尽量解析 Git commit，并把 commit 信息写入 lock。

## 6. 停止服务

停止某个服务的已知 backend 容器并移除网关路由：

```bash
uv run ov-mgn down alpha
```

`down` 不删除 `server.json`，也不移除源码、release 数据或 lock 文件。
成功后会更新 `state.json.lock`：清空该服务的 `candidate_release_id` 和
`online_release_id`，用于表示“已正常停服”而不是运行态不一致。

## 7. 查看和审计状态

查看完整状态：

```bash
uv run ov-mgn status
```

`status` 会同时输出原始文件状态和按服务聚合后的 `services_summary`。日常快捷
检查优先看 `services_summary.<service>`：

- `stage`：当前阶段，例如 `configured`、`planned`、`candidate`、
  `candidate_pending_promotion`、`online`、`disabled`、`orphaned`、
  `inconsistent`。
- `ok`：`true` 表示当前记录一致，`false` 表示存在不一致，`null` 表示跳过了
  Docker 检查，无法确认容器实际状态。
- `issues`：需要处理的问题列表。
- `candidate_release_id` / `online_release_id`：当前候选和线上 release。
- `containers`：实际 Docker backend 容器。
- `internal`：配置、计划、发布记录和运行记录的简要引用。
- `external`：Docker 是否已检查、backend/gateway 容器和 Nginx 路由结果。
- `gateway`：gateway 是否启用、稳定入口 URL、candidate 预览 URL、route path、
  服务目录 URL、服务目录 JSON URL、active/candidate release、Nginx 配置匹配结果和
  gateway 容器。

跳过 Docker 查询，只查看文件状态：

```bash
uv run ov-mgn status --no-docker
```

查看用户配置：

```bash
uv run ov-mgn config-file show
uv run ov-mgn config-file show services.alpha.openviking.env
```

查看隐藏的内部路径信息：

```bash
uv run ov-mgn server-config paths
```

`server-config` 是隐藏命令组，保留给内部和兼容用途，不作为主要用户入口。

## 8. 文件职责

默认文件位于 `~/.ov_mgn/`：

- `server.json`：用户维护的服务配置。
- `server.json.lock`：`plan` 生成的 candidate 部署计划，只读。
- `release.json.lock`：`promote` 写入的线上 release 记录，只读。
- `state.json.lock`：运行时状态，记录 candidate 和 online release id。
- `secrets.env`：用户维护的 Docker secret env 文件。

不要手动编辑 `server.json.lock`、`release.json.lock` 或 `state.json.lock`，除非是在故障
恢复场景下明确知道影响。日常配置变更只编辑 `server.json`。

### 状态层级

`ov-mgn` 把服务状态分成五层：

- 配置层：`server.json`，说明服务是否存在、是否启用、gateway 入口、route path、
  源码和 OpenViking 配置。
- 计划层：`server.json.lock`，由 `plan` 生成，冻结下一次 candidate 的
  release id、backend 容器名、route path 和数据目录。
- 发布层：`release.json.lock`，由 `promote` 写入，记录最后一次成功发布的
  online release。
- 运行记录层：`state.json.lock`，记录当前被 `ov-mgn` 认为正在运行的 candidate /
  online release id。
- 外部容器层：Docker backend 容器、gateway 容器、Nginx 路由和容器运行状态。

源码目录和 secret env 文件只在配置或计划阶段校验。`status` 暂不做 HTTP 健康
检查、业务探针或 OpenViking 应用级可用性判断。

容器内的 `ov` CLI 使用 `ovcli.conf` 读取服务地址和 API key。`ov-mgn` 会在渲染
`openviking.conf` 时读取 `server.root_api_key`，并生成同目录的 `ovcli.conf`：
其中 `url` 指向容器内 `http://127.0.0.1:{defaults.backend_port}`，`api_key` 与
`server.root_api_key` 保持一致。容器启动时会设置
`OPENVIKING_CLI_CONFIG_FILE=/app/config/ovcli.conf`，因此类似
`docker exec <container> ov ls` 的内部运维命令会使用同一把测试/部署 key。
`ovcli.conf` 位于 release 配置目录中，不写入 lock 或 status 输出。

### 服务阶段

`services_summary.<service>.stage` 的含义：

- `configured`：只存在用户配置，还没有生成计划或运行态。
- `disabled`：配置中 `enabled=false`。无运行态和容器时正常；仍有运行态或容器时
  `ok=false`。
- `planned`：存在 candidate 计划，但当前没有运行 release。
- `candidate`：`state.json.lock` 记录 candidate release，且与计划层匹配。
- `candidate_pending_promotion`：candidate 和 online 运行记录同时存在。
- `online`：`state.json.lock` 记录 online release，且与发布层匹配。
- `stopped`：语义上由 `down` 表达；当前 JSON 输出会回落为 `planned` 或
  `configured`，因为运行 release id 已清空。
- `orphaned`：lock、release、state 或 Docker 中有服务，但 `server.json` 中没有。
- `inconsistent`：release id、运行记录、容器、运行状态或 Nginx 路由不一致。

Docker 检查启用时，`status` 会检查：

- gateway 容器是否存在并 running。
- backend 容器的 role、release id、容器名和运行状态。
- Nginx 配置中的 route path 和 backend 目标是否匹配 `state.json.lock`。
- 容器缺失、非 running、release id 不匹配或路由目标不匹配都会写入 `issues`，
  并使 `ok=false`。

使用 `--no-docker` 时不检查外部容器层；如果服务处于运行中阶段，`ok` 为 `null`，
并在 `issues` 中输出 `docker inspection skipped`。

### 命令读写层级

| 命令 | 读取 | 写入 |
| --- | --- | --- |
| `config-file` | `server.json` | `server.json` |
| `plan` | `server.json` | `server.json.lock` |
| `up SERVICE` | `server.json.lock`、`release.json.lock`、`state.json.lock` | backend 容器、gateway 配置、`state.json.lock` |
| `promote SERVICE` | `server.json.lock`、`release.json.lock`、`state.json.lock` | gateway 路由、`release.json.lock`、`state.json.lock` |
| `switch SERVICE RELEASE_ID` | `server.json.lock`、`release.json.lock`、`state.json.lock`、Docker | gateway 路由、`release.json.lock`、`state.json.lock` |
| `down SERVICE` | `server.json.lock`、`release.json.lock`、`state.json.lock` | 删除已知容器、`state.json.lock` |
| `status` | `server.json`、`server.json.lock`、`release.json.lock`、`state.json.lock`、Docker | 无 |

## 9. 常见维护场景

### 修改 gateway 入口或服务路径

修改统一入口端口或某个服务的路径后，重新计划、启动 candidate 并 promote：

```bash
uv run ov-mgn config-file set defaults.gateway.port 18081
uv run ov-mgn config-file set services.alpha.route_path /alpha/
uv run ov-mgn config-file validate
uv run ov-mgn plan
uv run ov-mgn up alpha
uv run ov-mgn promote alpha
```

### 临时禁用服务

```bash
uv run ov-mgn config-file set services.alpha.enabled false
uv run ov-mgn config-file validate
uv run ov-mgn plan
```

如果已有容器仍在运行，需要显式停止：

```bash
uv run ov-mgn down alpha
```

### 清空 secret env 文件路径

使用 JSON `null`：

```bash
uv run ov-mgn config-file set defaults.secret_env_file null
uv run ov-mgn config-file validate
```

### 删除可选字段或映射键

```bash
uv run ov-mgn config-file unset services.alpha.image
uv run ov-mgn config-file unset services.alpha.openviking.template_path
uv run ov-mgn config-file unset services.alpha.openviking.env.TZ
uv run ov-mgn config-file unset services.alpha.openviking.vars.profile
```

不能用 `unset` 删除整个 service 或必需字段：

```bash
uv run ov-mgn config-file unset services.alpha
uv run ov-mgn config-file unset services.alpha.source
```

这些命令会失败，并保持原文件不变。

## 10. 故障处理

配置校验失败时，先查看错误路径和原因：

```bash
uv run ov-mgn config-file validate
```

常见原因：

- `defaults.gateway.enabled` 被设为 `false`。
- 两个服务使用了相同的 `route_path`。
- local source 路径不存在或不是目录。
- template path 不存在或不是文件。
- env key 不符合 `[A-Z_][A-Z0-9_]*`。

candidate 启动后没有切换线上时，可以查看状态：

```bash
uv run ov-mgn status
docker ps --filter label=ov-mgn.service=alpha
```

如果决定放弃 candidate，可以停止服务后重新生成计划：

```bash
uv run ov-mgn down alpha
uv run ov-mgn plan
uv run ov-mgn up alpha
```

如果 `promote` 失败，先不要手动删除数据目录。查看：

```bash
uv run ov-mgn status --no-docker
docker ps --filter label=ov-mgn.service=alpha
```

确认 backend、gateway、release lock 和 state 的状态后，再决定是重新
`promote`、`down`，还是人工介入 Docker。
