# maibot_dsh_bridge 🐾

> 让麦麦机器人（MaiBot）拥有 **DeepSeek Harness (DSH)** 重型智能体能力的官方桥接插件。

---

## ✨ 功能特性

- **双通信模式支持**：
  - 🟢 **原生 ACP 模式（开箱即用）**：麦麦直接拉起 `dsh --profile acp` 进程，通过标准 JSON-RPC stdio 管道驱动 DSH 智能体执行。
  - 🔵 **HTTP POST 模式（分布式跨机）**：通过向 DSH 的 `dsh-post-gateway` 插件发送 HTTP POST 请求，实现跨服务器调度。
- **全能力委托**：支持在群聊中触发 DSH 进行长文本代码编写、目录排查、沙盒命令运行与测试。
- **多会话隔离**：按群号 / 私聊 Session 自动维护 DSH 独立的上下文和会话状态。

---

## 🚀 快速上手

### 1. 原生 ACP 模式（推荐，本机同机运行）

确保环境中已安装 `dsh` 全局命令（Node.js ≥ 20）。
在群聊或私聊中发送：

```text
#dsh 请帮我检查一下 /main/app/github/dsh-pet/ 目录下的 git 状态并汇总
```

### 2. HTTP POST 模式（跨机器 / 远程调用）

1. 在目标 DSH 服务端安装网关插件：
   ```bash
   dsh plugin --profile web add link:/path/to/dsh-post-gateway
   ```
2. 在麦麦插件设置中将 `mode` 改为 `post`，并填写 `gateway_url`（如 `http://127.0.0.1:3080/api/dsh/v1`）。

---

## ⚙️ 配置项说明

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `plugin.enabled` | `true` | 是否启用插件 |
| `plugin.mode` | `acp` | 运行模式：`acp` (原生) 或 `post` (HTTP网关) |
| `plugin.trigger_prefix` | `#dsh` | 触发指令前缀 |
| `acp.dsh_bin` | `/home/a1/.npm-global/bin/dsh` | dsh 全局可执行文件路径 |
| `post.gateway_url` | `http://127.0.0.1:3080/api/dsh/v1` | DSH 网关 API 地址 |

---

## 📄 许可证

MIT License
