"""MaiBot Plugin Entry: DSH Bridge."""

import asyncio
from typing import Any, Dict, Optional, cast
from maibot_sdk import Field, HookHandler, MaiBotPlugin, PluginConfigBase
from maibot_sdk.types import HookMode

from .acp_client import DshAcpClient


class PluginSectionConfig(PluginConfigBase):
    __ui_label__ = "基础开关"
    __ui_icon__ = "settings"
    __ui_order__ = 0

    enabled: bool = Field(default=True, description="是否启用 DSH 智能体桥接插件")
    mode: str = Field(default="acp", description="通信模式: acp (原生stdio进程) 或 post (HTTP网关)")
    trigger_prefix: str = Field(default="#dsh", description="触发指令前缀，如 #dsh <任务描述>")


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


class DshBridgePlugin(MaiBotPlugin):
    config_model = DshBridgeConfig

    _acp_client: Optional[DshAcpClient] = None
    _sessions: Dict[str, str] = {}  # session_id (chat) -> DSH sessionId

    async def on_load(self) -> None:
        cfg = cast(DshBridgeConfig, self.config)
        self.ctx.logger.info("DSH Bridge 插件已加载，当前模式: %s", cfg.plugin.mode)
        if cfg.plugin.enabled and cfg.plugin.mode == "acp":
            await self._init_acp()

    async def on_unload(self) -> None:
        if self._acp_client:
            await self._acp_client.stop()
            self._acp_client = None
        self.ctx.logger.info("DSH Bridge 插件已卸载")

    async def _init_acp(self) -> None:
        cfg = cast(DshBridgeConfig, self.config).acp
        self._acp_client = DshAcpClient(
            dsh_bin=cfg.dsh_bin,
            cwd=cfg.default_cwd,
            logger=self.ctx.logger,
        )
        await self._acp_client.start()

    @HookHandler(
        "chat.receive.after_process",
        name="dsh_command_handler",
        description="检测群聊或私聊中的 #dsh 指令并委派给 DeepSeek Harness",
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

        if not text.startswith(prefix):
            return

        task_content = text[len(prefix):].strip()
        if not task_content:
            await self.ctx.send.text(
                f"🤖 DeepSeek Harness 指令格式：\n{prefix} <你的任务描述/代码需求/排查目标>",
                stream_id,
            )
            return

        # Prompt acknowledgment
        await self.ctx.send.text(f"🚀 正在将任务分派给 DeepSeek Harness 智能体执行中...\n任务: {task_content[:60]}...", stream_id)

        try:
            if cfg.plugin.mode == "acp":
                if not self._acp_client:
                    await self._init_acp()
                if not self._acp_client:
                    raise RuntimeError("ACP 客户端初始化失败")

                # Get or create DSH session
                dsh_session = self._sessions.get(stream_id)
                if not dsh_session:
                    dsh_session = await self._acp_client.create_session()
                    self._sessions[stream_id] = dsh_session

                # Run prompt
                result = await self._acp_client.prompt(dsh_session, task_content, timeout=cfg.acp.timeout)
                await self.ctx.send.text(f"✨ DeepSeek Harness 任务交付结果：\n\n{result}", stream_id)

            elif cfg.plugin.mode == "post":
                # HTTP POST invocation implementation (Phase 2)
                import urllib.request
                import json

                url = f"{cfg.post.gateway_url.rstrip('/')}/task"
                req_data = json.dumps({"prompt": task_content}).encode("utf-8")
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
                output = resp_json.get("output", resp_json.get("result", "(无返回结果)"))
                await self.ctx.send.text(f"✨ DeepSeek Harness 执行完成：\n\n{output}", stream_id)

        except Exception as e:
            self.ctx.logger.error("DSH 任务执行异常: %s", e, exc_info=True)
            await self.ctx.send.text(f"❌ DSH 任务执行失败: {e}", stream_id)
