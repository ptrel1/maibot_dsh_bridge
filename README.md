# maibot_dsh_bridge 🐾

> 让麦麦机器人（MaiBot）拥有 **DeepSeek Harness (DSH)** 重型智能体能力的官方桥接插件。

---

## ✨ 功能特性

- **双通信模式支持**：
  - 🔵 **HTTP POST 模式（推荐，直接复用当前运行中的 DSH 3080 实例）**：
    - 直接向本地或远程运行中的 DSH Web 发送请求，复用当前已配置的自定义模型（如 `gemini-3.7-flash-tiered`）、已装插件与鉴权凭据，零额外子进程开销！
    - **前置要求**：目标 DSH 需安装 [`dsh-postapi-bridge`](https://github.com/ptrel1/dsh-postapi-bridge) 插件。
  - 🟢 **原生 ACP 模式（单机独立子进程）**：
    - 麦麦直接拉起 `dsh --profile acp` 子进程，通过标准 JSON-RPC stdio 管道进行通信。
- **全场景自然语言意图感知**：
  - 支持前缀指令：`#dsh <任务>` 或 `dsh <任务>`；
  - 支持自然口语：*“让dsh去检查一下当前项目的git状态”*、*“用dsh帮我写个脚本”*；
  - 支持 Maisaka 大模型自主发起 `@Tool(dsh_execute_task)` 工具调用。
- **方案 B 智能会话分叉与继承（Session Router）**：
  - 自动比对新任务与历史任务的语义相似度；追问与相关任务自动继承上下文，跨工程任务自动开启干净独立沙盒。
- **方案 C 智能混合排版（Hybrid Renderer）**：
  - 短文本（<= 200 字）自动走 ASCII 结构化美化，方便一键复制；
  - 长篇分析 / 代码报告（> 200 字）通过原生 Pillow 极速渲染为 **GitHub Dark 高清卡片长图**，并附带执行耗时与模型状态胶囊徽章。
- **分级权限与安全防线**：
  - 内置管理员白名单（可写、可执行）；
  - 普通访客强制只读沙盒（防改文件、防密钥泄露）；
  - 全局绝对拦截 `rm -rf /`、格式化等高危破坏指令。
- **长任务守护与随时拉闸**：
  - 支持长达 30 分钟（1800s）深度任务；
  - 每 5 分钟自动推送中间进度心跳报告；
  - 用户可随时发送 `停止dsh` 或 `取消任务` 立即优雅中断。

---

## 🚀 快速上手

### 模式一：HTTP POST 模式（推荐，直连运行中的 DSH 3080）

1. **在 DSH 服务端安装网关插件**：
   ```bash
   # 在运行 DSH 的环境中执行
   dsh plugin --profile web add link:/path/to/dsh-postapi-bridge
   # 或通过 GitHub 安装
   dsh plugin --profile web add github:ptrel1/dsh-postapi-bridge#main
   ```
2. **在麦麦端配置 `config.toml`**：
   ```toml
   [plugin]
   mode = "post"
   
   [post]
   gateway_url = "http://127.0.0.1:3080/api/dsh/v1"
   token = "your_gateway_token"
   ```
3. 在群聊或私聊中直接艾特或发送：
   ```text
   #dsh 查看当前系统的内存和磁盘占用
   ```

---

### 模式二：原生 ACP 模式（单机独立子进程）

1. 确保环境中已安装 `dsh` 全局命令（Node.js ≥ 20）并在 `~/.dsh/.credentials.yaml` 中配置了 API Key；
2. 将 `config.toml` 中的 `mode` 改为 `"acp"` 即可。

---

## ⚙️ 配置项完整说明

```toml
[permissions]
admin_users = ["10001", "10002"]   # 管理员 QQ 白名单（拥有全功能特权，可修改代码与执行命令）
allow_guest_users = true           # 是否允许非管理员使用只读与咨询功能

[persona]
mode_name = "d_teacher"  # d_teacher (代码专家/三步法) 或 custom (自定义模式)
custom_system_prompt = "" # 自定义 System Prompt

[model]
provider = "deepseek-official"     # 模型服务提供方路由 (默认 DeepSeek 官方)
model = "deepseek-v4-flash"        # 执行大模型名称 (默认 Flash 极速模型)

[plugin]
enabled = true
mode = "acp"                        # 运行模式: acp (原生独立子进程) 或 post (直连运行中DSH)
trigger_prefix = "#dsh"
enable_natural_language = true      # 开启自然语言意图感知
block_critical_commands = true      # 拦截 rm -rf / 等高危指令
heartbeat_interval_sec = 300.0      # 每 5 分钟汇报一次心跳进度
max_timeout_sec = 1800.0           # 30 分钟最大保护上限
session_match_threshold = 0.45      # 相似度阈值 (方案B)
session_idle_expire_sec = 1800.0   # 30 分钟会话过期隔离

[acp]
dsh_bin = "dsh"                     # dsh 全局命令 (默认在系统 PATH 中查找)
default_cwd = "."                   # 默认执行沙盒目录

[post]
gateway_url = "http://127.0.0.1:3080/api/dsh/v1"
token = "your_gateway_token"
```

---

## 🛡️ 安全与权限规范说明

- **权限模式（`DSH_PERMISSION_MODE`）**：
  - 本插件默认支持在宿主安全策略下运行；
  - 若配置为 `danger-full-access`（全权限开发模式），DSH 智能体可直接读写跨目录与执行系统级测试。生产环境建议通过设置管理员白名单（`admin_users`）严格限制执行权限，并仅对可信用户开放代码编写与修改指令。

---

## 📄 许可证

MIT License
