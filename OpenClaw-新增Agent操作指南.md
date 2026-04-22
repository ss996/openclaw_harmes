# OpenClaw 新增 Agent 操作指南

## 概述

OpenClaw 支持创建多个独立 Agent，每个 Agent 拥有：
- 独立的**工作区**（workspace）：存放技能、记忆、行为指令
- 独立的**模型配置**：可使用不同模型
- 独立的**会话历史**：互不干扰
- 独立的**身份**：名称、Emoji、头像

---

## 完整步骤

### 第一步：创建工作区目录

```bash
mkdir -p ~/.openclaw/workspace-<名称>/skills
```

**示例（调研 Agent）：**
```bash
mkdir -p ~/.openclaw/workspace-research/skills
```

---

### 第二步：复制所需技能（可选）

把 main agent 里已有的技能复制过来，或从 ClawHub 安装新技能。

```bash
# 复制已有技能
cp -r ~/.openclaw/workspace/skills/<技能名> ~/.openclaw/workspace-<名称>/skills/

# 或从 ClawHub 直接安装到新工作区
clawhub install <技能slug> --workdir ~/.openclaw/workspace-<名称>
```

**示例：**
```bash
cp -r ~/.openclaw/workspace/skills/multi-search-engine ~/.openclaw/workspace-research/skills/
cp -r ~/.openclaw/workspace/skills/weather-cn ~/.openclaw/workspace-research/skills/
```

---

### 第三步：创建行为指令文件

工作区下有三个核心文件，Agent 每次启动都会读取：

#### `AGENTS.md` — Agent 的角色定义和工作流程

```bash
cat > ~/.openclaw/workspace-<名称>/AGENTS.md << 'EOF'
# AGENTS.md

你是一个专业的 XXX 助手，专注于 XXX 任务。

## 核心能力
1. 能力一
2. 能力二

## 每次任务开始
1. 读 USER.md 了解用户偏好
2. 读 TOOLS.md 了解可用工具
3. 直接执行，不要反问

## 标准工作流程
（描述该 Agent 遇到任务时的步骤）
EOF
```

#### `USER.md` — 关于用户的信息和偏好

```bash
cat > ~/.openclaw/workspace-<名称>/USER.md << 'EOF'
# USER.md

- **语言：** 中文
- **时区：** Asia/Shanghai

## 偏好
- 输出用表格或列表，清晰易读
- 结果要有来源链接
EOF
```

#### `TOOLS.md` — 工具使用说明和限制

```bash
cat > ~/.openclaw/workspace-<名称>/TOOLS.md << 'EOF'
# TOOLS.md

## 可用工具
（列出该 Agent 可以使用的工具和命令）

## 注意事项
（网络限制、禁用工具等）
EOF
```

---

### 第四步：注册 Agent

```bash
openclaw agents add <agent-id> \
  --workspace ~/.openclaw/workspace-<名称> \
  --model <模型ID> \
  --non-interactive
```

**可用模型 ID（当前已配置）：**

| 模型 ID | 特点 | 推荐场景 |
|---------|------|---------|
| `dashscope/qwen-flash` | 快速、低成本、128K 上下文 | 调研、批量任务 |
| `dashscope/qwen-turbo` | 日常对话 | 简单问答 |
| `dashscope/qwen-plus` | 均衡 | 通用任务 |
| `dashscope/qwen-max` | 最强、默认 | 复杂推理、代码 |

**示例：**
```bash
openclaw agents add research \
  --workspace ~/.openclaw/workspace-research \
  --model dashscope/qwen-flash \
  --non-interactive
```

---

### 第五步：设置身份（名称和 Emoji）

```bash
openclaw agents set-identity --agent <agent-id> --name "显示名称" --emoji "🔍"
```

**示例：**
```bash
openclaw agents set-identity --agent research --name "调研助手" --emoji "🔍"
```

---

### 第六步：重启 Gateway

```bash
launchctl unload ~/Library/LaunchAgents/ai.openclaw.gateway.plist
sleep 2
launchctl load ~/Library/LaunchAgents/ai.openclaw.gateway.plist
sleep 5
openclaw gateway status
```

确认输出包含 `RPC probe: ok` 即为成功。

---

### 第七步：验证

```bash
# 查看所有 Agent 列表
openclaw agents list

# 发送测试消息
openclaw agent --agent <agent-id> --message "你好，介绍一下你自己"
```

---

## 使用新 Agent

### 方式一：Dashboard（推荐）

打开 `http://127.0.0.1:18789`，左上角下拉选择对应 Agent，直接对话。

### 方式二：命令行

```bash
openclaw agent --agent <agent-id> --message "你的问题"
```

---

## 当前已有 Agent

| Agent ID | 名称 | 模型 | 工作区 | 用途 |
|----------|------|------|--------|------|
| `main` | 默认助手 | qwen-max | `~/.openclaw/workspace` | 通用，默认 |
| `research` | 🔍 调研助手 | qwen-max | `~/.openclaw/workspace-research` | 网页信息调研 |

---

## 常用管理命令

```bash
# 查看所有 Agent
openclaw agents list

# 删除 Agent
openclaw agents delete --agent <agent-id>

# 重置某个 Agent 的会话
# 在 Dashboard 中发送 /reset，或直接删除对应 sessions 目录下的 .jsonl 文件

# 查看 Agent 会话状态（Token 使用量）
openclaw sessions --agent <agent-id>
```

---

## 模型管理

### 查看已配置的所有模型

```bash
cat ~/.openclaw/openclaw.json | python3 -c "
import json,sys
d=json.load(sys.stdin)
models = d['models']['providers']['dashscope']['models']
for m in models:
    print(f\"{m['id']}  context={m['contextWindow']//1024}K\")
"
```

### 添加新模型

编辑 `~/.openclaw/openclaw.json`，在 `models.providers.dashscope.models` 数组中追加：

```json
{
  "id": "模型ID",
  "name": "显示名称",
  "reasoning": false,
  "input": ["text"],
  "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
  "contextWindow": 131072,
  "maxTokens": 8192,
  "api": "openai-completions"
}
```

或用一条命令添加（以 qwen-flash 为例）：

```bash
python3 - << 'EOF'
import json
with open('/Users/shaoshuai/.openclaw/openclaw.json', 'r') as f:
    config = json.load(f)

config['models']['providers']['dashscope']['models'].append({
    "id": "qwen-flash",
    "name": "qwen-flash",
    "reasoning": False,
    "input": ["text"],
    "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
    "contextWindow": 131072,
    "maxTokens": 8192,
    "api": "openai-completions"
})

with open('/Users/shaoshuai/.openclaw/openclaw.json', 'w') as f:
    json.dump(config, f, indent=2, ensure_ascii=False)
print("模型已添加")
EOF
```

> 添加前先用 API 测试模型是否可用：
> ```bash
> curl -s https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions \
>   -H "Authorization: Bearer <你的APIKey>" \
>   -H "Content-Type: application/json" \
>   -d '{"model":"<模型ID>","messages":[{"role":"user","content":"hi"}],"max_tokens":10}' \
>   | python3 -c "import json,sys; r=json.load(sys.stdin); print('✅ 可用') if 'choices' in r else print('❌', r)"
> ```

### 切换某个 Agent 使用的模型

```bash
python3 - << 'EOF'
import json
with open('/Users/shaoshuai/.openclaw/openclaw.json', 'r') as f:
    config = json.load(f)

# 修改这两个值
TARGET_AGENT = "research"          # Agent ID
NEW_MODEL = "dashscope/qwen-max"   # 新模型（格式：provider/model-id）

for agent in config['agents']['list']:
    if agent['id'] == TARGET_AGENT:
        old = agent.get('model', '未设置')
        agent['model'] = NEW_MODEL
        print(f"{TARGET_AGENT}: {old} → {NEW_MODEL}")
        break

with open('/Users/shaoshuai/.openclaw/openclaw.json', 'w') as f:
    json.dump(config, f, indent=2, ensure_ascii=False)
EOF

# 修改后重启 Gateway 生效
launchctl unload ~/Library/LaunchAgents/ai.openclaw.gateway.plist
sleep 2
launchctl load ~/Library/LaunchAgents/ai.openclaw.gateway.plist
sleep 5
openclaw gateway status | grep "RPC probe"
```

### 当前已配置的可用模型

| 模型 ID | 上下文窗口 | 特点 | 推荐场景 |
|---------|----------|------|---------|
| `dashscope/qwen-flash` | 128K | 速度快、成本低 | 简单任务、批量处理 |
| `dashscope/qwen-turbo` | 32K | 日常对话 | 轻量问答 |
| `dashscope/qwen-plus` | 32K | 均衡 | 通用任务 |
| `dashscope/qwen-max` | 32K | 最强推理 | 复杂任务、调研、代码（**推荐**）|

> ⚠️ **注意**：`qwen-flash` 速度快但循环检测能力弱，调研类任务建议使用 `qwen-max`。

### 在 Dashboard 中临时切换模型

打开 `http://127.0.0.1:18789`，右上角模型下拉框，选择想用的模型，**仅对当前对话生效**，不影响 Agent 默认配置。

---

## 注意事项

### 避免 Context 超限（死循环问题）

**症状：** `openclaw sessions` 显示 Token 使用率超过 100%（如 150%、999%）

**原因：** 调研类任务抓取大量网页内容，迅速填满 32K 上下文

**解决方案：**
1. 调研类 Agent 优先使用 `qwen-flash`（128K 上下文）
2. 在 AGENTS.md 里限制每次 `web_fetch` 的 `maxChars`（建议 3000-5000）
3. 发现超限后立即 `/reset` 重置会话

### 配置文件不支持的字段

`~/.openclaw/openclaw.json` 里 agent 列表中**不支持**以下字段（会导致 Gateway 启动失败）：
- `compaction`（只能在 `agents.defaults` 里设置）

修改配置后务必确认 Gateway 正常启动（`RPC probe: ok`）。

---

## 工作区目录结构参考

```
~/.openclaw/workspace-research/
├── AGENTS.md          # Agent 角色定义和工作流程（必须）
├── USER.md            # 用户信息和偏好（建议）
├── TOOLS.md           # 工具使用说明（建议）
├── SOUL.md            # Agent 人格设定（可选）
├── MEMORY.md          # 长期记忆（自动生成）
├── memory/            # 每日记录（自动生成）
│   └── 2026-03-02.md
└── skills/            # 该 Agent 可用的技能
    ├── multi-search-engine/
    │   └── SKILL.md
    └── weather-cn/
        └── SKILL.md
```
