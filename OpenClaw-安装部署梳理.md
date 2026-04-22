# OpenClaw 安装部署梳理（本机）

更新时间：2026-02-28

## 1. 这次完成了什么

- 通过 `npm install -g openclaw@latest` 全局安装了 OpenClaw CLI。
- 完成了本地初始化（onboard），安装并启用了 Gateway 守护服务（macOS LaunchAgent）。
- 配置了百炼（DashScope）OpenAI 兼容接口作为模型提供方。
- 将默认模型设置为 `dashscope/qwen-turbo`。
- 修复了 Gateway 不可达与 Dashboard 认证限流问题，最终验证可正常对话。

## 2. 你当前“已配置”的内容

当前主配置文件：`~/.openclaw/openclaw.json`

关键配置（已脱敏）：

- `models.providers.dashscope.baseUrl`: `https://dashscope.aliyuncs.com/compatible-mode/v1`
- `models.providers.dashscope.api`: `openai-completions`
- `models.providers.dashscope.apiKey`: `sk-***`（已配置，文件中为明文）
- `models.providers.dashscope.models[0].id`: `qwen-turbo`
- `models.providers.dashscope.models[0].contextWindow`: `32768`
- `models.providers.dashscope.models[0].maxTokens`: `8192`
- `agents.defaults.model`: `dashscope/qwen-turbo`
- `agents.defaults.workspace`: `/Users/shaoshuai/.openclaw/workspace`
- `gateway.port`: `18789`
- `gateway.bind`: `loopback`（仅本机可访问）
- `gateway.auth.mode`: `token`
- `gateway.auth.token`: `***`（已配置）
- `gateway.tailscale.mode`: `off`

## 3. 安装在哪里

CLI 与全局包（nvm Node 环境下）：

- 可执行文件：`/Users/shaoshuai/.nvm/versions/node/v22.16.0/bin/openclaw`
- 实际入口（软链目标）：`/Users/shaoshuai/.nvm/versions/node/v22.16.0/lib/node_modules/openclaw/openclaw.mjs`
- 全局 npm 包目录：`/Users/shaoshuai/.nvm/versions/node/v22.16.0/lib/node_modules`

OpenClaw 运行时数据目录：

- 配置：`/Users/shaoshuai/.openclaw/openclaw.json`
- Workspace：`/Users/shaoshuai/.openclaw/workspace`
- Sessions：`/Users/shaoshuai/.openclaw/agents/main/sessions/sessions.json`
- 网关日志（运行时）：`/tmp/openclaw/openclaw-2026-02-28.log`

服务（macOS）：

- LaunchAgent 文件：`~/Library/LaunchAgents/ai.openclaw.gateway.plist`
- 服务监听：`127.0.0.1:18789`

## 4. 当时是怎么配置的（命令路径）

核心操作路径如下：

1) 全局安装

```bash
npm install -g openclaw@latest
```

2) 初始化（无交互）

```bash
openclaw onboard --non-interactive --accept-risk --install-daemon --flow quickstart --auth-choice skip --skip-channels --skip-skills
```

3) 接入百炼（OpenAI compatible）

```bash
openclaw onboard \
  --non-interactive \
  --accept-risk \
  --mode local \
  --install-daemon \
  --auth-choice custom-api-key \
  --custom-compatibility openai \
  --custom-base-url "https://dashscope.aliyuncs.com/compatible-mode/v1" \
  --custom-provider-id dashscope \
  --custom-model-id qwen-turbo \
  --custom-api-key "$DASHSCOPE_API_KEY" \
  --skip-channels
```

4) 由于 `qwen-turbo` 初始上下文被写成 4096，被 OpenClaw 最低门槛（16000）拦截，后续修正为可用值：

```bash
openclaw config set "models.providers.dashscope.models[0].contextWindow" 32768
openclaw config set "models.providers.dashscope.models[0].maxTokens" 8192
openclaw config set "agents.defaults.model" "dashscope/qwen-turbo"
```

5) 重启并验证服务

```bash
openclaw gateway restart
openclaw gateway probe
openclaw status
```

## 5. 如何修改这些配置

有两种方式：

- 向导方式（推荐）：
  - `openclaw onboard`（首配/重配）
  - `openclaw configure`（按模块调参）
- 命令方式（精准改某个键）：
  - 查看：`openclaw config get <path>`
  - 设置：`openclaw config set <path> <value>`
  - 删除：`openclaw config unset <path>`

常见修改示例：

```bash
# 修改默认模型
openclaw config set agents.defaults.model "dashscope/qwen-turbo"

# 改端口
openclaw config set gateway.port 18789

# 改认证模式（谨慎）
openclaw config set gateway.auth.mode token

# 重启生效
openclaw gateway restart
```

## 6. 当前运行环境

- 操作系统：macOS 26.3 (arm64)
- Node：v22.16.0
- npm：10.9.2
- pnpm：7.33.1
- OpenClaw：2026.2.26
- Gateway：
  - 运行方式：LaunchAgent 常驻
  - 监听地址：`127.0.0.1:18789`
  - 访问方式：`openclaw dashboard`（会带 token）

## 7. 会不会影响别的程序或项目

结论：**影响范围较小，但有几个注意点**。

- 会影响的范围：
  - 全局安装了 `openclaw` 命令（在当前 nvm Node 版本下）。
  - 启动了一个常驻服务，长期监听本机 `127.0.0.1:18789`。
  - 写入了用户级目录 `~/.openclaw/*`（不在当前项目目录内）。

- 通常不会影响：
  - 其他普通项目代码与依赖（除非它们也要占用 18789 端口）。
  - 其他程序网络暴露（当前 bind=loopback，仅本机访问）。

- 可能冲突点：
  - 端口冲突：若其他程序用 `18789`，会冲突。
  - nvm Node 版本切换：OpenClaw 服务绑定当前 nvm 路径，切换/删除该 Node 版本后服务可能失效。

## 8. 安全建议（强烈）

- 你曾在聊天中暴露过 DashScope Key，建议立即在百炼控制台旋转（重置）API Key。
- 旋转后更新配置中的 `models.providers.dashscope.apiKey`。
- 平时尽量用环境变量注入敏感信息，避免明文出现在命令历史或配置文件。
