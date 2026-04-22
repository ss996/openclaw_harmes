# TOOLS.md 来源说明与恢复

## 为什么沙箱里会有「What Goes Here」那段描述？

- 你看到的 `~/.openclaw/sandboxes/agent-main-f331f052/TOOLS.md` 里的内容，**前半段**（从 `# TOOLS.md - Local Notes` 到 `This is your cheat sheet.`）来自 **OpenClaw 官方默认模板**。
- 模板在源码/安装包里的位置：
  - 英文：`docs/reference/templates/TOOLS.md`
  - 仓库：<https://github.com/openclaw/openclaw/blob/main/docs/reference/templates/TOOLS.md>
- 首次创建 workspace 或执行 `openclaw setup` / `openclaw onboard` 时，会把这份模板写入 **`~/.openclaw/workspace/TOOLS.md`**。
- **沙箱**（如 `~/.openclaw/sandboxes/agent-main-xxx/`）是「某次会话」的隔离目录，里面的 `TOOLS.md` 是从 **workspace** 复制过去的，所以会带上你在 workspace 里后来加的内容（多 Agent 协作、网络搜索、本机环境等）。

因此：  
**「这样的描述」= 官方默认模板 + 你之前在 workspace 里追加的自定义段落。**

---

## 已做的恢复

- 已将 **`~/.openclaw/workspace/TOOLS.md`** 恢复为 **仅包含官方默认模板** 的版本（与 [OpenClaw 官方 TOOLS 模板](https://github.com/openclaw/openclaw/blob/main/docs/reference/templates/TOOLS.md) 正文一致，不含 YAML frontmatter）。
- 之后新起的会话/沙箱会从 workspace 重新拷贝，所以**新沙箱里的 TOOLS.md 会自动变成恢复后的内容**。
- 当前已存在的沙箱目录里的 `TOOLS.md` 不会自动改；若希望一致，可手动用 workspace 里的内容覆盖，或忽略（该沙箱下次可能被重建）。

---

## 若想保留之前的自定义内容

恢复前 workspace 的 TOOLS.md 里还有这些**你自己加的段落**（已不在当前文件里）：

1. **查看已安装 Skill 的内容**（强调直接读本地文件、不要用 `clawhub inspect`）
2. **ClawHub Skill 安装**（用 `clawhub --workdir ~/.openclaw/workspace install ...`）
3. **多 Agent 协作**（委托给 research agent、`openclaw agent --agent research --message "..."`）
4. **网络搜索**（禁止 `web_search`、用百度/搜狗 `web_fetch`）
5. **本机环境**（macOS、conda 路径、Cursor CLI 等）

若需要，可以：
- 从备份或历史里把上述内容找回来，再**追加**到当前的 `~/.openclaw/workspace/TOOLS.md` 末尾；或
- 在 workspace 的 TOOLS.md 里重新写一版精简的「本地备注」，只保留你真正要 Agent 遵守的几条。

---

## 如何再次拿到「纯官方」TOOLS 模板

- 从安装包复制（Node 全局安装时）：
  ```bash
  cp "$(dirname $(which openclaw))/../lib/node_modules/openclaw/docs/reference/templates/TOOLS.md" ~/.openclaw/workspace/TOOLS.md
  ```
  若路径不同，可先 `npm root -g` 找到全局 node_modules，再进入 `openclaw/docs/reference/templates/TOOLS.md`。
- 或从 GitHub 复制正文：  
  <https://github.com/openclaw/openclaw/blob/main/docs/reference/templates/TOOLS.md>  
  （粘贴时去掉开头的 YAML `---...---` 块即可。）
