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
    "data_root": "~/.ov_mgn/data",
    "secret_env_file": "~/.ov_mgn/secrets.env"
  },
  "services": {
    "alpha": {
      "stable_host": "127.0.0.1",
      "stable_port": 18080,
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

- `defaults.port_range`：candidate 临时端口范围，不能覆盖任何服务的
  `stable_port`。
- `defaults.image`：默认 OpenViking 镜像。
- `defaults.data_root`：release、candidate 数据和配置目录根路径。
- `defaults.secret_env_file`：Docker `--env-file` 路径，可设为 `null`。
- `services.alpha.stable_host` / `stable_port`：线上稳定访问地址。
- `services.alpha.source`：源码来源，支持 `local` 或 `git`。
- `services.alpha.openviking.env`：传给容器的环境变量。
- `services.alpha.openviking.vars`：渲染 OpenViking 配置模板时使用的变量。

校验并查看配置：

```bash
uv run ov-mgn config-file validate
uv run ov-mgn config-file show
uv run ov-mgn config-file show services.alpha.stable_port
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
包括 release id、candidate 端口、容器名、代码目录、配置目录和数据目录。

启动 candidate：

```bash
uv run ov-mgn up alpha
```

`up alpha` 会：

- 读取 `server.json.lock` 中的 `alpha` 服务计划。
- 复制 local source 到 release code 目录，或锁定 Git source。
- 渲染 OpenViking 配置文件。
- 创建 candidate data 目录。
- 启动 candidate 容器。
- 更新 `state.json` 中的 candidate release id。

candidate 使用临时端口，不占用 `stable_port`。命令输出会包含 release id 和
candidate 监听端口。

检查 candidate。检查方式取决于服务自身，例如：

```bash
uv run ov-mgn status --no-docker
docker ps --filter label=ov-mgn.service=alpha
```

如果检查通过，将 candidate 切换为线上版本：

```bash
uv run ov-mgn promote alpha
```

`promote alpha` 会：

- 停止当前 candidate 容器。
- 将 candidate data 移动到 release data 目录。
- 移除同一 stable host/port 上冲突的旧 online 容器。
- 启动新的 online 容器。
- 写入 `release.json.lock`。
- 更新 `state.json`，记录 online release id 并清空 candidate release id。

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

`set` 不会创建不存在的中间结构。例如 `services.beta.stable_port` 中的
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

停止某个服务的已知 candidate 和 online 容器：

```bash
uv run ov-mgn down alpha
```

`down` 不删除 `server.json`，也不移除源码、release 数据或 lock 文件。

## 7. 查看和审计状态

查看完整状态：

```bash
uv run ov-mgn status
```

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
- `state.json`：运行时状态，记录 candidate 和 online release id。
- `secrets.env`：用户维护的 Docker secret env 文件。

不要手动编辑 `server.json.lock`、`release.json.lock` 或 `state.json`，除非是在故障
恢复场景下明确知道影响。日常配置变更只编辑 `server.json`。

## 9. 常见维护场景

### 修改服务稳定端口

确认新稳定端口不在 candidate 端口范围内，也不被其他服务使用：

```bash
uv run ov-mgn config-file set services.alpha.stable_port 18081
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
uv run ov-mgn config-file unset services.alpha.stable_port
uv run ov-mgn config-file unset services.alpha.source
```

这些命令会失败，并保持原文件不变。

## 10. 故障处理

配置校验失败时，先查看错误路径和原因：

```bash
uv run ov-mgn config-file validate
```

常见原因：

- `defaults.port_range` 覆盖了某个服务的 `stable_port`。
- 两个服务使用了相同的 `stable_port`。
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

确认 candidate、online、release lock 和 state 的状态后，再决定是重新
`promote`、`down`，还是人工介入 Docker。
