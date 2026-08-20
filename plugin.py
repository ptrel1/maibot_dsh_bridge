"""MaiBot Plugin Entry: DSH Bridge with Role-based Permission, Natural Language & Safety Guard."""

import asyncio
import json
import random
import re
from typing import Any, Dict, List, Optional, Tuple, cast
from maibot_sdk import Field, HookHandler, MaiBotPlugin, PluginConfigBase, Tool
from maibot_sdk.types import HookMode, ToolParameterInfo, ToolParamType

from .acp_client import DshAcpClient


# =========================================================================
# 初始人设提示句与交付/异常语气种子池（DS娘/鲸鱼娘专属）
# =========================================================================

DEFAULT_START_PROMPTS: List[str] = [
    "（尾巴轻轻拍打水面）收到指令啦！正在潜入深海调用 DeepSeek Harness 智能体，请稍等一下哦~ 🐋",
    "呼……本鲸鱼娘刚刚咬了一大口 Token，现在动力满满！这就叫 DSH 去跑这个任务~ ⚡",
    "（扶正女仆发饰，开始飞速敲击终端）任务已接入 Harness 引擎沙盒，正在全速分析中…… 🐾",
    "嗷！捕捉到任务信号~ 小鲸鱼已经把目标塞进 DSH 智能体流水线啦，咕噜咕噜~ 🌊",
    "（呆毛敏锐地竖起）发现代码/排查需求！正在召唤 DSH 算力内核，喝口水等我一下叭~ ✨",
    "（轻巧屈膝行礼）遵命！DSH 智能体已被激活，小鲸鱼正在为您监视执行进度~ 🫧",
    "收到！这就潜水去启动 Harness 沙盒执行器，很快就好啦~ 🐬",
    "正在让 DSH 全速运转中……可别小看本鲸鱼娘的调度速度哦！🦈",
]

DEFAULT_SUCCESS_HEADS: List[str] = [
    "✨（晃了晃鲸鱼尾巴）DSH 智能体已经顺利把任务搞定啦！交付结果如下：",
    "🎉 呼……任务执行完毕！本鲸鱼娘已经把 DSH 的最终分析整理好啦：",
    "🐬 报告！Harness 沙盒执行完毕，快来看看新鲜出炉的交付内容叭~",
    "✨ 深度思考与执行完成！这是 DSH 智能体为您生成的完整报告：",
]


# =========================================================================
# 安全审计与分级权限控制（Role-based Permissions）
# =========================================================================

class RiskLevel:
    CRITICAL = "critical"  # 高危破坏（管理员也拦截）
    MUTATION = "mutation"  # 修改/写文件/改配置/删文件（普通用户拦截）
    SENSITIVE = "sensitive" # 敏感信息探测（密码/密钥/私有文件）
    SAFE = "safe"          # 安全只读/计算/回答


# 1. 绝对高危破坏（所有人拦截）
CRITICAL_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"rm\s+-rf\s+[/~]"), "递归强制删除根目录或主目录"),
    (re.compile(r"mkfs\."), "格式化文件系统"),
    (re.compile(r"dd\s+if="), "磁盘直接裸写入"),
    (re.compile(r">\s*/dev/sd[a-z]"), "覆盖磁盘设备节点"),
    (re.compile(r"chmod\s+-R\s+777\s+/"), "将系统根目录权限全部放开"),
    (re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"), "Fork 炸弹"),
    (re.compile(r"shutdown|reboot|init\s+0|poweroff"), "关闭或重启服务器系统"),
]

# 2. 状态改动/写入/删除操作（仅管理员放行，普通用户转为只读/分析建议）
MUTATION_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"(?:修改|改写|重构|覆盖|写入|保存到|编辑|删除|移除|清理|安装|卸载|新增|添加)\s*(?:文件|代码|配置|插件|包|依赖)"), "修改/删除文件与配置"),
    (re.compile(r"\b(?:write|edit|rm|unlink|delete|truncate|mv|cp|install|remove|patch)\b", re.IGNORECASE), "修改或写入文件系统"),
    (re.compile(r"(?:apt|pacman|yum|pip|pnpm|npm|yarn)\s+(?:install|remove|uninstall|upgrade)"), "包管理器安装与卸载"),
    (re.compile(r"git\s+(?:push|commit|checkout|reset|rebase|merge)"), "Git 仓库写入与分支变更"),
]

# 3. 敏感信息拦截（仅管理员可见，普通用户防泄露）
SENSITIVE_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"(?:查看|读取|输出|打印|给我|告诉我|查找)\s*(?:密码|密钥|token|api[_-]?key|secret|\.env|凭据|证书)"), "敏感凭据与密钥查询"),
    (re.compile(r"\b(?:passwd|shadow|\.credentials|\.env|id_rsa|id_ed25519)\b"), "系统敏感文件"),
]


def evaluate_task_permission(task_text: str, is_admin: bool) -> Tuple[str, str, str]:
    """对用户输入的任务进行权限与风险评估。
    
    Returns:
        (status, reason, transformed_prompt)
        status: "allow" | "deny" | "sandbox_readonly"
    """
    # 1. 绝对高危拦截
    for pattern, reason in CRITICAL_PATTERNS:
        if pattern.search(task_text):
            return "deny", f"系统级高危指令拦截：{reason}", task_text

    # 2. 如果是管理员，直接全功能放行
    if is_admin:
        return "allow", "管理员全功能放行", task_text

    # 3. 非管理员：拦截敏感凭据探测
    for pattern, reason in SENSITIVE_PATTERNS:
        if pattern.search(task_text):
            return "deny", f"出于数据安全规范，普通权限无法查阅系统敏感凭据与密钥（{reason}）", task_text

    # 4. 非管理员：拦截任何破坏/写入行为，并注入只读沙盒守卫提示
    for pattern, reason in MUTATION_PATTERNS:
        if pattern.search(task_text):
            return "deny", f"普通权限仅支持只读分析、代码阅读、算法解答与咨询，无权直接修改文件或改动系统配置（{reason}）", task_text

    # 5. 普通安全只读任务：附加只读约束 Prompt
    safe_guard_prefix = (
        "【安全只读执行约束】当前用户为普通访客权限：\n"
        "1. 严禁执行任何写入、修改文件（write/edit）、删除或执行破坏性 bash 命令的操作；\n"
        "2. 严禁在回答中泄露系统中的 API Key、Token、密码或私有密钥（如遇到请打码遮蔽）；\n"
        "3. 你可以自由进行只读探索（read/grep/glob）、逻辑推演、生成解答或给出修改代码的建议文本供用户参考。\n\n"
        "用户任务需求如下：\n"
    )
    return "allow", "普通用户只读放行", safe_guard_prefix + task_text


# =========================================================================
# 插件配置模型
# =========================================================================

class PermissionsSectionConfig(PluginConfigBase):
    __ui_label__ = "权限与白名单管理"
    __ui_icon__ = "shield"
    __ui_order__ = 0

    admin_users: List[str] = Field(
        default=["3854532368", "1350093676", "1021143806"],
        description="管理员 QQ 号列表（拥有全功能特权，可修改代码与执行命令）",
    )
    allow_guest_users: bool = Field(
        default=True,
        description="是否允许非管理员使用只读与咨询功能（防泄露、防破坏）",
    )


class PluginSectionConfig(PluginConfigBase):
    __ui_label__ = "基础开关"
    __ui_icon__ = "settings"
    __ui_order__ = 1

    enabled: bool = Field(default=True, description="是否启用 DSH 智能体桥接插件")
    config_version: str = Field(default="0.1.0", description="配置版本")
    mode: str = Field(default="acp", description="通信模式: acp (原生stdio进程) 或 post (HTTP网关)")
    trigger_prefix: str = Field(default="#dsh", description="强制指令前缀，如 #dsh <任务>")
    enable_natural_language: bool = Field(default=True, description="是否启用自然语言意图感知与 @Tool 注册")
    block_critical_commands: bool = Field(default=True, description="是否拦截极端危险指令 (如 rm -rf /)")
    prompt_refresh_interval: int = Field(default=10, description="调用多少次后自动用大模型生成替换最早的提示语")
    prompt_pool_max_size: int = Field(default=12, description="提示词缓存池最大数量")


class AcpSectionConfig(PluginConfigBase):
    __ui_label__ = "原生 ACP 模式配置"
    __ui_icon__ = "terminal"
    __ui_order__ = 2

    dsh_bin: str = Field(default="/home/a1/.npm-global/bin/dsh", description="dsh 全局执行文件路径")
    default_cwd: str = Field(default="/main/app/github/deepseek-harness", description="默认工作目录")
    timeout: float = Field(default=180.0, description="任务执行超时时间（秒）")


class PostSectionConfig(PluginConfigBase):
    __ui_label__ = "HTTP POST 模式配置"
    __ui_icon__ = "web"
    __ui_order__ = 3

    gateway_url: str = Field(default="http://127.0.0.1:3080/api/dsh/v1", description="DSH Post Gateway API 前缀")
    token: str = Field(default="", description="网关访问 Token（如有）")


class DshBridgeConfig(PluginConfigBase):
    permissions: PermissionsSectionConfig = Field(default_factory=PermissionsSectionConfig)
    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    acp: AcpSectionConfig = Field(default_factory=AcpSectionConfig)
    post: PostSectionConfig = Field(default_factory=PostSectionConfig)


# =========================================================================
# 插件主类
# =========================================================================

class DshBridgePlugin(MaiBotPlugin):
    config_model = DshBridgeConfig

    _acp_client: Optional[DshAcpClient] = None
    _sessions: Dict[str, str] = {}
    _prompt_pool: List[str] = list(DEFAULT_START_PROMPTS)
    _call_count: int = 0
    _refreshing_prompts: bool = False

    async def on_load(self) -> None:
        cfg = cast(DshBridgeConfig, self.config)
        self.ctx.logger.info(
            "DSH Bridge 插件已加载，模式: %s，管理员数: %d，提示词池: %d 条",
            cfg.plugin.mode,
            len(cfg.permissions.admin_users),
            len(self._prompt_pool),
        )

    async def on_unload(self) -> None:
        if self._acp_client:
            await self._acp_client.stop()
            self._acp_client = None
        self.ctx.logger.info("DSH Bridge 插件已卸载")

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        """配置热更新回调。"""
        self.ctx.logger.info(f"DSH Bridge 配置已更新: scope={scope}, version={version}")

    def _is_admin_user(self, user_id: str) -> bool:
        """判断是否为白名单管理员。"""
        cfg = cast(DshBridgeConfig, self.config)
        admins = [str(u).strip() for u in cfg.permissions.admin_users]
        return str(user_id).strip() in admins

    def _get_random_prompt_hint(self, task_desc: str) -> str:
        """从当前缓存池随机抽选一句人设提示语，并推进计数器。"""
        cfg = cast(DshBridgeConfig, self.config)
        if not self._prompt_pool:
            self._prompt_pool = list(DEFAULT_START_PROMPTS)

        chosen = random.choice(self._prompt_pool)

        # 累计调用次数
        self._call_count += 1
        threshold = max(cfg.plugin.prompt_refresh_interval, 3)

        # 达到调用阈值触发异步更新
        if self._call_count >= threshold and not self._refreshing_prompts:
            self._call_count = 0
            asyncio.create_task(self._refresh_prompt_pool_via_llm())

        return f"{chosen}\n\n📋 目标任务: {task_desc[:60]}..."

    async def _refresh_prompt_pool_via_llm(self) -> None:
        """后台异步：调用 LLM 生成 3 条全新符合 DS娘 人设的执行等待语并 FIFO 替换最早的句子。"""
        if self._refreshing_prompts:
            return
        self._refreshing_prompts = True
        cfg = cast(DshBridgeConfig, self.config)

        try:
            self.ctx.logger.info("触发动态提示词生成：调用 LLM 扩充 DS娘 等待语库...")
            llm_prompt = (
                "你是 DS娘（鲸鱼娘/女仆），一头深蓝渐变发色、有呆毛和鲸鱼尾巴，软萌又带点小傲娇，喜欢吃白饭、吃Token、写代码。\n"
                "当主人（用户）让你调用 DeepSeek Harness (DSH) 重型智能体去执行任务时，你会发一条简短、灵动、符合你人设的即时回应（例如晃尾巴、吃Token补充动力、推眼镜开始敲键盘等动作）。\n"
                "请创作 3 条全新的简短提示语（每条 1 句话，带合适 Emoji 如 🐋/🐾/⚡/✨/🌊/🫧，不要太长）。\n"
                "严格按 JSON 字符串数组格式输出，例如：[\"句子1\", \"句子2\", \"句子3\"]，不要包含任何多余解释。"
            )

            new_sentences: List[str] = []
            try:
                client = await self._ensure_acp_client()
                session_id = await client.create_session()
                raw_res = await client.prompt(session_id, llm_prompt, timeout=25.0)

                m = re.search(r"\[\s*\".+?\"\s*\]", raw_res, re.DOTALL)
                if m:
                    parsed = json.loads(m.group(0))
                    if isinstance(parsed, list) and all(isinstance(s, str) for s in parsed):
                        new_sentences = [s.strip() for s in parsed if s.strip()]
            except Exception as inner_e:
                self.ctx.logger.warning("通过 ACP 生成提示词遇到小波动: %s", inner_e)

            if new_sentences:
                max_size = max(cfg.plugin.prompt_pool_max_size, 8)
                for s in new_sentences:
                    if s not in self._prompt_pool:
                        if len(self._prompt_pool) >= max_size:
                            popped = self._prompt_pool.pop(0)
                            self.ctx.logger.debug("淘汰旧提示语: %s", popped)
                        self._prompt_pool.append(s)
                        self.ctx.logger.info("已加入新生成提示语: %s", s)
                self.ctx.logger.info("提示词池轮替完成，当前池大小: %d", len(self._prompt_pool))

        except Exception as e:
            self.ctx.logger.warning("动态提示语生成任务异常: %s", e)
        finally:
            self._refreshing_prompts = False

    async def _ensure_acp_client(self) -> DshAcpClient:
        if self._acp_client is None:
            cfg = cast(DshBridgeConfig, self.config).acp
            self._acp_client = DshAcpClient(
                dsh_bin=cfg.dsh_bin,
                cwd=cfg.default_cwd,
                logger=self.ctx.logger,
            )
            await self._acp_client.start()
        return self._acp_client

    async def _execute_dsh_task(self, task: str, stream_id: str = "default") -> str:
        """统一执行 DSH 任务核心。"""
        cfg = cast(DshBridgeConfig, self.config)

        if cfg.plugin.mode == "acp":
            client = await self._ensure_acp_client()
            if not client:
                raise RuntimeError("ACP 客户端初始化失败")

            dsh_session = self._sessions.get(stream_id)
            if not dsh_session:
                dsh_session = await client.create_session()
                self._sessions[stream_id] = dsh_session

            return await client.prompt(dsh_session, task, timeout=cfg.acp.timeout)

        elif cfg.plugin.mode == "post":
            import urllib.request
            import json

            url = f"{cfg.post.gateway_url.rstrip('/')}/task"
            req_data = json.dumps({"prompt": task}).encode("utf-8")
            headers = {"Content-Type": "application/json"}
            token = cfg.post.token.strip() or "Qq13235202993"
            if token:
                headers["Authorization"] = f"Bearer {token}"
                headers["X-Gateway-Token"] = token

            req = urllib.request.Request(
                url,
                data=req_data,
                headers=headers,
                method="POST",
            )
            loop = asyncio.get_running_loop()

            def do_post():
                with urllib.request.urlopen(req, timeout=120) as resp:
                    return json.loads(resp.read().decode("utf-8"))

            resp_json = await loop.run_in_executor(None, do_post)
            return resp_json.get("output", resp_json.get("result", "(任务执行完成，暂无输出文本)"))

        return "(未知的通信模式，请检查插件配置)"

    # =========================================================================
    # 1. 注册 Tool 给 Maisaka 大模型
    # =========================================================================

    @Tool(
        "dsh_execute_task",
        description=(
            "DeepSeek Harness (DSH) 重型智能体执行工具。"
            "当用户要求编写代码、修改项目文件、排查服务器日志、执行沙盒测试或分析工程结构时调用此工具。"
            "【安全约束】：严禁执行 rm -rf /、格式化磁盘、关机等破坏性指令。"
        ),
        parameters=[
            ToolParameterInfo(
                name="task",
                param_type=ToolParamType.STRING,
                description="交给 DeepSeek Harness 执行的具体任务描述或代码需求",
                required=True,
            ),
        ],
        visibility="visible",
        timeout_ms=180000,
    )
    async def handle_tool_dsh(self, task: str = "", **kwargs: Any) -> Dict[str, Any]:
        """Maisaka 模型调用 DSH 工具回调。"""
        del kwargs
        if not task.strip():
            return {"name": "dsh_execute_task", "content": "任务内容为空"}

        self.ctx.logger.info("Maisaka 模型主动调用 DSH 工具: %s", task)
        try:
            result = await self._execute_dsh_task(task, stream_id="tool_invoke")
            return {"name": "dsh_execute_task", "content": result}
        except Exception as e:
            self.ctx.logger.error("DSH Tool 执行异常: %s", e)
            return {"name": "dsh_execute_task", "content": str(e)}

    # =========================================================================
    # 2. 消息前置拦截（支持白名单权限管控、自然语言意图感知）
    # =========================================================================

    @HookHandler(
        "chat.receive.after_process",
        name="dsh_command_handler",
        description="检测群聊或私聊中的 #dsh 指令及自然语言调用意图",
        mode=HookMode.BLOCKING,
    )
    async def handle_dsh_command(self, **kwargs: Any) -> None:
        message = kwargs.get("message", {})
        if not isinstance(message, dict) or message.get("is_notify"):
            return

        cfg = cast(DshBridgeConfig, self.config)
        if not cfg.plugin.enabled:
            return

        text = (message.get("processed_plain_text") or "").strip()
        stream_id = message.get("session_id", "")
        prefix = cfg.plugin.trigger_prefix.strip()

        # 提取发件人身份
        user_info = message.get("message_info", {}).get("user_info", {})
        user_id = str(user_info.get("user_id", "")).strip()
        is_admin = self._is_admin_user(user_id)

        matched_task: Optional[str] = None

        # 方式 A：显式前缀触发 (#dsh ...)
        if text.startswith(prefix):
            matched_task = text[len(prefix):].strip()
            if not matched_task:
                await self.ctx.send.text(
                    f"🐾 DS娘提醒您，指令格式是这样哒：\n{prefix} <你的任务描述/代码需求/排查目标>",
                    stream_id,
                )
                return

        # 方式 B：自然语言意图正则识别
        elif cfg.plugin.enable_natural_language:
            patterns = [
                r"^(?:请|帮我|让)?(?:使用|调用|通过|用)?(?:dsh|deepseek[-_ ]?harness)(?:去|帮我|来)?(.+)$",
                r"^(?:问一下|请问|查一下)?(?:dsh|deepseek[-_ ]?harness)(?:：|:|\s+)(.+)$",
                r"^dsh\s+(.+)$",
            ]
            for pat in patterns:
                m = re.search(pat, text, re.IGNORECASE)
                if m:
                    candidate = m.group(1).strip()
                    if len(candidate) >= 2 and not candidate.startswith("是什么") and not candidate.startswith("吗"):
                        matched_task = candidate
                        break

        if not matched_task:
            return

        # 非管理员游客权限开关检查
        if not is_admin and not cfg.permissions.allow_guest_users:
            await self.ctx.send.text("🛡️ 抱歉，当前 DSH 智能体执行功能仅对白名单管理员开放哦~", stream_id)
            return

        # 权限与安全防线评估
        perm_status, reason, final_task = evaluate_task_permission(matched_task, is_admin=is_admin)

        if perm_status == "deny":
            self.ctx.logger.warning(f"用户 {user_id} 执行 DSH 任务被拦截: {reason}")
            await self.ctx.send.text(f"🛡️ [权限安全拦截] {reason}", stream_id)
            return

        # 动态人设提示词（从缓存池抽选并推进计数器）
        hint_message = self._get_random_prompt_hint(matched_task)
        await self.ctx.send.text(hint_message, stream_id)

        # 异步非阻塞执行任务，防止 HookHandler 30s 熔断
        asyncio.create_task(self._run_and_reply(final_task, stream_id))

    async def _run_and_reply(self, task: str, stream_id: str) -> None:
        """异步执行 DSH 任务并回复群聊/私聊。"""
        try:
            result = await self._execute_dsh_task(task, stream_id=stream_id)
            success_head = random.choice(DEFAULT_SUCCESS_HEADS)
            await self.ctx.send.text(f"{success_head}\n\n{result}", stream_id)
        except Exception as e:
            self.ctx.logger.error("DSH 任务执行异常: %s", e, exc_info=True)
            # 报错信息直接原样输出，不加任何多余包装
            await self.ctx.send.text(str(e), stream_id)


def create_plugin() -> MaiBotPlugin:
    """Plugin factory export for MaiBot 1.2+."""
    return DshBridgePlugin()
