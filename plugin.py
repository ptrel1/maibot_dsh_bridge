"""MaiBot Plugin Entry: DSH Bridge with Natural Language, Safety Guard & @Tool Support."""

import asyncio
import re
from typing import Any, Dict, List, Optional, Tuple, cast
from maibot_sdk import Field, HookHandler, MaiBotPlugin, PluginConfigBase, Tool
from maibot_sdk.types import HookMode, ToolParameterInfo, ToolParamType

from .acp_client import DshAcpClient


# =========================================================================
# 安全审计与风险评估模块（参考自 mai_study_code）
# =========================================================================

class RiskLevel:
    CRITICAL = "critical"  # 高危破坏（禁止执行）
    HIGH = "high"          # 高风险操作
    MEDIUM = "medium"      # 中度风险
    LOW = "low"            # 安全操作


CRITICAL_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"rm\s+-rf\s+[/~]"), "递归强制删除根目录或主目录"),
    (re.compile(r"mkfs\."), "格式化文件系统"),
    (re.compile(r"dd\s+if="), "磁盘直接裸写入"),
    (re.compile(r">\s*/dev/sd[a-z]"), "覆盖磁盘设备节点"),
    (re.compile(r"chmod\s+-R\s+777\s+/"), "将系统根目录权限全部放开"),
    (re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"), "Fork 炸弹"),
    (re.compile(r"shutdown|reboot|init\s+0|poweroff"), "关闭或重启服务器系统"),
]

HIGH_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"DROP\s+DATABASE", re.IGNORECASE), "删除数据库"),
    (re.compile(r"TRUNCATE\s+TABLE", re.IGNORECASE), "清空数据表"),
    (re.compile(r"iptables\s+-F"), "清空防火墙规则"),
    (re.compile(r"pkill\s+-9|killall\s+-9"), "强制批量杀死系统进程"),
]


def evaluate_task_safety(task_text: str) -> Tuple[str, str]:
    """对用户输入的任务或代码进行快速安全风险评估。"""
    for pattern, reason in CRITICAL_PATTERNS:
        if pattern.search(task_text):
            return RiskLevel.CRITICAL, reason
    for pattern, reason in HIGH_PATTERNS:
        if pattern.search(task_text):
            return RiskLevel.HIGH, reason
    return RiskLevel.LOW, "安全"


# =========================================================================
# 插件配置模型
# =========================================================================

class PluginSectionConfig(PluginConfigBase):
    __ui_label__ = "基础开关"
    __ui_icon__ = "settings"
    __ui_order__ = 0

    enabled: bool = Field(default=True, description="是否启用 DSH 智能体桥接插件")
    config_version: str = Field(default="0.1.0", description="配置版本")
    mode: str = Field(default="acp", description="通信模式: acp (原生stdio进程) 或 post (HTTP网关)")
    trigger_prefix: str = Field(default="#dsh", description="强制指令前缀，如 #dsh <任务>")
    enable_natural_language: bool = Field(default=True, description="是否启用自然语言意图感知与 @Tool 注册")
    block_critical_commands: bool = Field(default=True, description="是否拦截极端危险指令 (如 rm -rf /)")


class AcpSectionConfig(PluginConfigBase):
    __ui_label__ = "原生 ACP 模式配置"
    __ui_icon__ = "terminal"
    __ui_order__ = 1

    dsh_bin: str = Field(default="/home/a1/.npm-global/bin/dsh", description="dsh 全局执行文件路径")
    default_cwd: str = Field(default="/main/app/github/deepseek-harness", description="默认工作目录")
    timeout: float = Field(default=180.0, description="任务执行超时时间（秒）")


class PostSectionConfig(PluginConfigBase):
    __ui_label__ = "HTTP POST 模式配置"
    __ui_icon__ = "web"
    __ui_order__ = 2

    gateway_url: str = Field(default="http://127.0.0.1:3080/api/dsh/v1", description="DSH Post Gateway API 前缀")
    token: str = Field(default="", description="网关访问 Token（如有）")


class DshBridgeConfig(PluginConfigBase):
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

    async def on_load(self) -> None:
        cfg = cast(DshBridgeConfig, self.config)
        self.ctx.logger.info("DSH Bridge 插件已加载，当前模式: %s", cfg.plugin.mode)

    async def on_unload(self) -> None:
        if self._acp_client:
            await self._acp_client.stop()
            self._acp_client = None
        self.ctx.logger.info("DSH Bridge 插件已卸载")

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        """配置热更新回调。"""
        self.ctx.logger.info(f"DSH Bridge 配置已更新: scope={scope}, version={version}")

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

        # 安全防线拦截
        if cfg.plugin.block_critical_commands:
            risk_level, reason = evaluate_task_safety(task)
            if risk_level == RiskLevel.CRITICAL:
                self.ctx.logger.warning(f"DSH 指令被安全防线拦截: {reason}")
                return f"🛡️ [安全防线拦截] 拒绝执行高危操作：{reason}"

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
            req = urllib.request.Request(
                url,
                data=req_data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            loop = asyncio.get_running_loop()

            def do_post():
                with urllib.request.urlopen(req, timeout=120) as resp:
                    return json.loads(resp.read().decode("utf-8"))

            resp_json = await loop.run_in_executor(None, do_post)
            return resp_json.get("output", resp_json.get("result", "(无返回结果)"))

        return "(未知的通信模式)"

    # =========================================================================
    # 1. 注册 Tool 给 Maisaka 大模型（带安全防线与能力描述）
    # =========================================================================

    @Tool(
        "dsh_execute_task",
        description=(
            "DeepSeek Harness (DSH) 重型智能体执行工具。"
            "当用户要求编写代码、修改项目文件、排查服务器日志、执行沙盒测试或分析工程结构时调用此工具。"
            "【安全约束】：禁止执行 rm -rf /、格式化磁盘、关机等破坏性指令。"
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
            return {"name": "dsh_execute_task", "content": f"DSH 执行失败: {e}"}

    # =========================================================================
    # 2. 消息前置拦截（支持 #dsh 前缀与自然语言意图快速唤起）
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

        matched_task: Optional[str] = None

        # 方式 A：显式前缀触发 (#dsh ...)
        if text.startswith(prefix):
            matched_task = text[len(prefix):].strip()
            if not matched_task:
                await self.ctx.send.text(
                    f"🤖 DeepSeek Harness 指令格式：\n{prefix} <你的任务描述/代码需求/排查目标>",
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
                    # 过滤纯日常问句（如"你能连上dsh吗"等）
                    if len(candidate) >= 2 and not candidate.startswith("是什么") and not candidate.startswith("吗"):
                        matched_task = candidate
                        break

        if not matched_task:
            return

        # 发送处理中提示
        await self.ctx.send.text(f"🚀 正在将任务分派给 DeepSeek Harness 智能体执行中...\n任务: {matched_task[:60]}...", stream_id)

        # 异步非阻塞执行任务，防止 HookHandler 30s 熔断
        asyncio.create_task(self._run_and_reply(matched_task, stream_id))

    async def _run_and_reply(self, task: str, stream_id: str) -> None:
        """异步执行 DSH 任务并回复群聊/私聊。"""
        try:
            result = await self._execute_dsh_task(task, stream_id=stream_id)
            await self.ctx.send.text(f"✨ DeepSeek Harness 任务交付结果：\n\n{result}", stream_id)
        except Exception as e:
            self.ctx.logger.error("DSH 任务执行异常: %s", e, exc_info=True)
            await self.ctx.send.text(f"❌ DSH 任务执行失败: {e}", stream_id)


def create_plugin() -> MaiBotPlugin:
    """Plugin factory export for MaiBot 1.2+."""
    return DshBridgePlugin()
