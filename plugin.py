"""MaiBot Plugin Entry: DSH Bridge with Configurable Model, Persona, Non-blocking @Tool & Card Rendering."""

import asyncio
import difflib
import json
import random
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, cast
from maibot_sdk import Field, HookHandler, MaiBotPlugin, PluginConfigBase, Tool
from maibot_sdk.types import HookMode, ToolParameterInfo, ToolParamType

from .acp_client import DshAcpClient
from .formatter import format_markdown_to_clean_text
from .image_renderer import render_markdown_to_card_image


# =========================================================================
# 初始人设提示句与交付语气种子池（DS娘/鲸鱼娘专属）
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
# 方案 B：任务历史记录与智能会话匹配/分叉（Session Matching & Router）
# =========================================================================

class SessionHistoryRecord:
    """保存每个历史任务的元数据，用于后续相关性匹配。"""

    def __init__(self, session_id: str, task_summary: str, full_prompt: str, created_at: float):
        self.session_id = session_id
        self.task_summary = task_summary
        self.full_prompt = full_prompt
        self.last_used_at = created_at
        self.turn_count = 1


def calculate_task_similarity(new_task: str, old_task: str) -> float:
    """基于词重合度与字符序列比对计算任务相似度 (0.0 ~ 1.0)。"""
    if not new_task or not old_task:
        return 0.0
    seq_ratio = difflib.SequenceMatcher(None, new_task, old_task).ratio()
    
    paths_new = set(re.findall(r"[\w\-\./]+/[^\s,，。]+", new_task))
    paths_old = set(re.findall(r"[\w\-\./]+/[^\s,，。]+", old_task))
    path_overlap = len(paths_new & paths_old) / max(len(paths_new | paths_old), 1) if (paths_new or paths_old) else 0.0

    return 0.6 * seq_ratio + 0.4 * path_overlap


# =========================================================================
# 安全审计与分级权限控制（Role-based Permissions）
# =========================================================================

class RiskLevel:
    CRITICAL = "critical"  # 高危破坏（管理员也拦截）
    MUTATION = "mutation"  # 修改/写文件/改配置/删文件（普通用户拦截）
    SENSITIVE = "sensitive" # 敏感信息探测（密码/密钥/私有文件）
    SAFE = "safe"          # 安全只读/计算/回答


CRITICAL_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"rm\s+-rf\s+[/~]"), "递归强制删除根目录或主目录"),
    (re.compile(r"mkfs\."), "格式化文件系统"),
    (re.compile(r"dd\s+if="), "磁盘直接裸写入"),
    (re.compile(r">\s*/dev/sd[a-z]"), "覆盖磁盘设备节点"),
    (re.compile(r"chmod\s+-R\s+777\s+/"), "将系统根目录权限全部放开"),
    (re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"), "Fork 炸弹"),
    (re.compile(r"shutdown|reboot|init\s+0|poweroff"), "关闭或重启服务器系统"),
]

MUTATION_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"(?:修改|改写|重构|覆盖|写入|保存到|编辑|删除|移除|清理|安装|卸载|新增|添加)\s*(?:文件|代码|配置|插件|包|依赖)"), "修改/删除文件与配置"),
    (re.compile(r"\b(?:write|edit|rm|unlink|delete|truncate|mv|cp|install|remove|patch)\b", re.IGNORECASE), "修改或写入文件系统"),
    (re.compile(r"(?:apt|pacman|yum|pip|pnpm|npm|yarn)\s+(?:install|remove|uninstall|upgrade)"), "包管理器安装与卸载"),
    (re.compile(r"git\s+(?:push|commit|checkout|reset|rebase|merge)"), "Git 仓库写入与分支变更"),
]

SENSITIVE_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"(?:查看|读取|输出|打印|给我|告诉我|查找)\s*(?:密码|密钥|token|api[_-]?key|secret|\.env|凭据|证书)"), "敏感凭据与密钥查询"),
    (re.compile(r"\b(?:passwd|shadow|\.credentials|\.env|id_rsa|id_ed25519)\b"), "系统敏感文件"),
]


def evaluate_task_permission(task_text: str, is_admin: bool) -> Tuple[str, str, str]:
    """对用户输入的任务进行权限与风险评估。"""
    for pattern, reason in CRITICAL_PATTERNS:
        if pattern.search(task_text):
            return "deny", f"系统级高危指令拦截：{reason}", task_text

    if is_admin:
        return "allow", "管理员全功能放行", task_text

    for pattern, reason in SENSITIVE_PATTERNS:
        if pattern.search(task_text):
            return "deny", f"出于数据安全规范，普通权限无法查阅系统敏感凭据与密钥（{reason}）", task_text

    for pattern, reason in MUTATION_PATTERNS:
        if pattern.search(task_text):
            return "deny", f"普通权限仅支持只读分析、代码阅读、算法解答与咨询，无权直接修改文件或改动系统配置（{reason}）", task_text

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


class PersonaSectionConfig(PluginConfigBase):
    __ui_label__ = "DSH 角色与提示词模式"
    __ui_icon__ = "user-check"
    __ui_order__ = 1

    mode_name: str = Field(
        default="d_teacher",
        description="DSH 执行模式：d_teacher (代码专家/三步法) 或 custom (自定义模式)",
    )
    custom_system_prompt: str = Field(
        default="",
        description="自定义模式下的 System Prompt 前置系统提示词（为空时使用 D 老师默认三步法与规范）",
    )


class ModelSectionConfig(PluginConfigBase):
    __ui_label__ = "DSH 智能体执行模型"
    __ui_icon__ = "cpu"
    __ui_order__ = 2

    provider: str = Field(
        default="maiapi2",
        description="模型服务提供方路由（如 maiapi2 / deepseek-official）",
    )
    model: str = Field(
        default="gemini-3.7-flash-tiered",
        description="执行模型名称（如 gemini-3.7-flash-tiered / deepseek-v4-flash / deepseek-v4-pro）",
    )


class PluginSectionConfig(PluginConfigBase):
    __ui_label__ = "基础开关"
    __ui_icon__ = "settings"
    __ui_order__ = 3

    enabled: bool = Field(default=True, description="是否启用 DSH 智能体桥接插件")
    config_version: str = Field(default="0.1.0", description="配置版本")
    mode: str = Field(default="acp", description="通信模式: acp (原生stdio进程) 或 post (HTTP网关)")
    trigger_prefix: str = Field(default="#dsh", description="强制指令前缀，如 #dsh <任务>")
    enable_natural_language: bool = Field(default=True, description="是否启用自然语言意图感知与 @Tool 注册")
    block_critical_commands: bool = Field(default=True, description="是否拦截极端危险指令 (如 rm -rf /)")
    prompt_refresh_interval: int = Field(default=10, description="调用多少次后自动用大模型生成替换最早的提示语")
    prompt_pool_max_size: int = Field(default=12, description="提示词缓存池最大数量")
    heartbeat_interval_sec: float = Field(default=300.0, description="长任务周期汇报间隔（秒，默认5分钟/300秒）")
    max_timeout_sec: float = Field(default=1800.0, description="任务最大超时上限（秒，默认30分钟/1800秒）")
    session_match_threshold: float = Field(default=0.45, description="方案B：智能会话匹配阈值（高于此值继承历史会话，否则新建独立会话）")
    session_idle_expire_sec: float = Field(default=1800.0, description="方案B：会话空闲过期时间（秒，默认30分钟未互动自动隔离）")


class AcpSectionConfig(PluginConfigBase):
    __ui_label__ = "原生 ACP 模式配置"
    __ui_icon__ = "terminal"
    __ui_order__ = 4

    dsh_bin: str = Field(default="/home/a1/.npm-global/bin/dsh", description="dsh 全局执行文件路径")
    default_cwd: str = Field(default="/main/app/github/deepseek-harness", description="默认工作目录")


class PostSectionConfig(PluginConfigBase):
    __ui_label__ = "HTTP POST 模式配置"
    __ui_icon__ = "web"
    __ui_order__ = 5

    gateway_url: str = Field(default="http://127.0.0.1:3080/api/dsh/v1", description="DSH Post Gateway API 前缀")
    token: str = Field(default="Qq13235202993", description="网关访问 Token")


class DshBridgeConfig(PluginConfigBase):
    permissions: PermissionsSectionConfig = Field(default_factory=PermissionsSectionConfig)
    persona: PersonaSectionConfig = Field(default_factory=PersonaSectionConfig)
    model: ModelSectionConfig = Field(default_factory=ModelSectionConfig)
    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    acp: AcpSectionConfig = Field(default_factory=AcpSectionConfig)
    post: PostSectionConfig = Field(default_factory=PostSectionConfig)


# =========================================================================
# 插件主类
# =========================================================================

class DshBridgePlugin(MaiBotPlugin):
    config_model = DshBridgeConfig

    _acp_client: Optional[DshAcpClient] = None
    
    # 方案 B 会话池映射: stream_id -> List[SessionHistoryRecord]
    _chat_session_history: Dict[str, List[SessionHistoryRecord]] = {}
    
    _prompt_pool: List[str] = list(DEFAULT_START_PROMPTS)
    _call_count: int = 0
    _refreshing_prompts: bool = False

    # 活跃中的任务句柄映射: stream_id -> (dsh_session, asyncio.Task)
    _active_tasks: Dict[str, Tuple[str, asyncio.Task]] = {}

    # 防重复触发记录: stream_id -> (last_task_text, timestamp)
    _recent_triggers: Dict[str, Tuple[str, float]] = {}

    # 记录每个会话流最近活跃的 session_id（供 @Tool 快速定位目标群/私聊）
    _last_stream_id: str = ""

    async def on_load(self) -> None:
        cfg = cast(DshBridgeConfig, self.config)
        self.ctx.logger.info(
            "DSH Bridge 插件已加载，模型: %s/%s，模式: %s，提示词池: %d 条",
            cfg.model.provider,
            cfg.model.model,
            cfg.plugin.mode,
            len(self._prompt_pool),
        )

    async def on_unload(self) -> None:
        if self._acp_client:
            await self._acp_client.stop()
            self._acp_client = None
        for _, (_, t) in self._active_tasks.items():
            t.cancel()
        self._active_tasks.clear()
        self._chat_session_history.clear()
        self._recent_triggers.clear()
        self.ctx.logger.info("DSH Bridge 插件已卸载")

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        """配置热更新回调。"""
        self.ctx.logger.info(f"DSH Bridge 配置已更新: scope={scope}, version={version}")

    def _is_admin_user(self, user_id: str) -> bool:
        """判断是否为白名单管理员。"""
        cfg = cast(DshBridgeConfig, self.config)
        admins = [str(u).strip() for u in cfg.permissions.admin_users]
        return str(user_id).strip() in admins

    def _check_and_record_duplicate(self, stream_id: str, task_text: str, dedupe_window_sec: float = 10.0) -> bool:
        """检测短时间内是否有完全相同的任务重复触发。"""
        now = time.time()
        normalized_task = task_text.strip()
        last_entry = self._recent_triggers.get(stream_id)

        if last_entry:
            last_task, last_time = last_entry
            if last_task == normalized_task and (now - last_time) < dedupe_window_sec:
                self.ctx.logger.info("检测到重复指令 (在 %.1fs 内): '%s'，已自动去重忽略", now - last_time, normalized_task[:40])
                return True

        self._recent_triggers[stream_id] = (normalized_task, now)
        return False

    def _get_random_prompt_hint(self, task_desc: str, session_action: str = "new") -> str:
        """从当前缓存池随机抽选一句人设提示语，并附带模型与上下文继承说明。"""
        cfg = cast(DshBridgeConfig, self.config)
        if not self._prompt_pool:
            self._prompt_pool = list(DEFAULT_START_PROMPTS)

        chosen = random.choice(self._prompt_pool)

        # 累计调用次数
        self._call_count += 1
        threshold = max(cfg.plugin.prompt_refresh_interval, 3)

        if self._call_count >= threshold and not self._refreshing_prompts:
            self._call_count = 0
            asyncio.create_task(self._refresh_prompt_pool_via_llm())

        action_hint = "🔗 [继承历史会话上下文]" if session_action == "resume" else "✨ [已为您开启独立干净会话]"
        model_info = f"🧠 运行模型: {cfg.model.model}"
        return f"{chosen}\n\n📋 目标: {task_desc[:60]}...\n{model_info} | {action_hint}\n💡 提示：如需中途停止可发「停止dsh」，如需强制新会话可发「#dsh new」"

    async def _resolve_smart_session(self, stream_id: str, task: str) -> Tuple[str, str]:
        """方案 B 核心裁决器：计算相似度并决定继承历史 Session 或创建新独立 Session。"""
        client = await self._ensure_acp_client()
        cfg = cast(DshBridgeConfig, self.config)
        history_list = self._chat_session_history.setdefault(stream_id, [])
        now = time.time()

        continuation_patterns = [r"^(?:继续|接着|刚才|上一条|再改|在这个基础上|顺便把)", r"(?:刚才|之前|上面).*(?:修改|代码|文件|结果)"]
        is_explicit_continue = any(re.search(pat, task) for pat in continuation_patterns)

        if is_explicit_continue and history_list:
            latest_record = history_list[-1]
            latest_record.last_used_at = now
            latest_record.turn_count += 1
            self.ctx.logger.info("命中显式追问意图，继承最近 Session: %s", latest_record.session_id)
            return latest_record.session_id, "resume"

        best_record: Optional[SessionHistoryRecord] = None
        best_score = 0.0
        expire_sec = cfg.plugin.session_idle_expire_sec

        for rec in reversed(history_list):
            if (now - rec.last_used_at) > expire_sec:
                continue
            score = calculate_task_similarity(task, rec.task_summary)
            if score > best_score:
                best_score = score
                best_record = rec

        threshold = cfg.plugin.session_match_threshold
        if best_record and best_score >= threshold:
            best_record.last_used_at = now
            best_record.turn_count += 1
            best_record.task_summary += f" -> {task[:30]}"
            self.ctx.logger.info("智能命中历史任务 (相似度 %.2f >= %.2f)，继承 Session: %s", best_score, threshold, best_record.session_id)
            return best_record.session_id, "resume"

        new_session_id = await client.create_session()
        new_record = SessionHistoryRecord(
            session_id=new_session_id,
            task_summary=task[:80],
            full_prompt=task,
            created_at=now,
        )
        history_list.append(new_record)
        if len(history_list) > 15:
            history_list.pop(0)

        self.ctx.logger.info("开启全新独立 Session: %s (最高相似度 %.2f < %.2f)", new_session_id, best_score, threshold)
        return new_session_id, "new"

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
            cfg = cast(DshBridgeConfig, self.config)
            self._acp_client = DshAcpClient(
                dsh_bin=cfg.acp.dsh_bin,
                cwd=cfg.acp.default_cwd,
                provider=cfg.model.provider,
                model=cfg.model.model,
                logger=self.ctx.logger,
            )
            await self._acp_client.start()
        return self._acp_client

    async def _execute_dsh_task(
        self,
        task: str,
        stream_id: str = "default",
        dsh_session: Optional[str] = None,
        progress_cb: Optional[Callable[[str, int], Any]] = None,
    ) -> str:
        """统一执行 DSH 任务核心（支持自定义模型注入）。"""
        cfg = cast(DshBridgeConfig, self.config)

        final_prompt = task
        if cfg.persona.mode_name == "custom" and cfg.persona.custom_system_prompt.strip():
            custom_head = cfg.persona.custom_system_prompt.strip()
            final_prompt = f"【系统指令】\n{custom_head}\n\n【用户任务】\n{task}"

        if cfg.plugin.mode == "acp":
            client = await self._ensure_acp_client()
            if not client:
                raise RuntimeError("ACP 客户端初始化失败")

            if not dsh_session:
                dsh_session, _ = await self._resolve_smart_session(stream_id, task)

            current_task = asyncio.current_task()
            if current_task:
                self._active_tasks[stream_id] = (dsh_session, current_task)

            heartbeat_interval = max(cfg.plugin.heartbeat_interval_sec, 60.0)
            max_timeout = max(cfg.plugin.max_timeout_sec, 180.0)

            async def _heartbeat_worker():
                elapsed = 0
                while True:
                    await asyncio.sleep(heartbeat_interval)
                    elapsed += int(heartbeat_interval)
                    if progress_cb:
                        accumulated = ""
                        listener = client._session_listeners.get(dsh_session)
                        if listener and not listener.empty():
                            accumulated = f"(最新中间输出: {listener._queue[-1][:120]}...)" if listener._queue else ""
                        try:
                            msg = (
                                f"⌛ [DSH 任务执行中 · 已耗时 {elapsed // 60} 分钟]\n"
                                f"（晃了晃尾巴）DSH 仍在全力计算中，模型: {cfg.model.model}，小鲸鱼持续为您盯梢中~ 🫧\n"
                                f"{accumulated}\n"
                                f"💡 如需提前结束，可随时输入「停止dsh」"
                            )
                            await progress_cb(msg, elapsed)
                        except Exception as hb_err:
                            self.ctx.logger.warning("发送心跳进度异常: %s", hb_err)

            hb_task = asyncio.create_task(_heartbeat_worker())

            try:
                return await client.prompt(dsh_session, final_prompt, timeout=max_timeout)
            finally:
                hb_task.cancel()
                self._active_tasks.pop(stream_id, None)

        elif cfg.plugin.mode == "post":
            import urllib.request
            import json

            url = f"{cfg.post.gateway_url.rstrip('/')}/task"
            req_data = json.dumps({"prompt": final_prompt, "model": cfg.model.model, "provider": cfg.model.provider}).encode("utf-8")
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
                with urllib.request.urlopen(req, timeout=int(cfg.plugin.max_timeout_sec)) as resp:
                    return json.loads(resp.read().decode("utf-8"))

            resp_json = await loop.run_in_executor(None, do_post)
            return resp_json.get("output", resp_json.get("result", "(任务执行完成，暂无输出文本)"))

        return "(未知的通信模式，请检查插件配置)"

    # =========================================================================
    # 1. 注册 Tool 给 Maisaka 大模型（完全非阻塞：毫秒级返回，后台异步交付）
    # =========================================================================

    @Tool(
        "dsh_execute_task",
        description=(
            "DeepSeek Harness (DSH) 重型智能体执行工具。"
            "当用户要求编写代码、修改项目文件、排查服务器日志、执行沙盒测试或分析工程结构时调用此工具。"
            "【特点】：该工具会在后台异步启动 DSH 深度推理与执行，并自动向用户汇报进度与交付结果。"
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
        """Maisaka 模型调用 DSH 工具回调（立即返回确认，后台启动长任务）。"""
        del kwargs
        if not task.strip():
            return {"name": "dsh_execute_task", "content": "任务内容为空"}

        stream_id = self._last_stream_id or "default"

        # 防重校验
        if self._check_and_record_duplicate(stream_id, task, dedupe_window_sec=10.0):
            return {
                "name": "dsh_execute_task",
                "content": "任务已在后台执行中，无需重复派发。你可以直接告诉用户'任务正在后台处理中'。",
            }

        self.ctx.logger.info("Maisaka 模型调用 DSH 工具 (非阻塞派发): %s", task)

        # 启动后台异步任务并自动推送到当前会话流
        asyncio.create_task(self._run_and_reply(task, stream_id))

        return {
            "name": "dsh_execute_task",
            "content": f"任务已成功在后台分派给 DSH 智能体执行。小鲸鱼正在为您持续监视并在完成后自动发送报告，你可以直接告诉用户'任务已在后台启动'。",
        }

    # =========================================================================
    # 2. 消息前置拦截（泛化自然语言意图感知）
    # =========================================================================

    @HookHandler(
        "chat.receive.after_process",
        name="dsh_command_handler",
        description="检测群聊或私聊中的 #dsh 指令、停止请求及自然语言调用意图",
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
        self._last_stream_id = stream_id
        prefix = cfg.plugin.trigger_prefix.strip()

        # 提取发件人身份
        user_info = message.get("message_info", {}).get("user_info", {})
        user_id = str(user_info.get("user_id", "")).strip()
        is_admin = self._is_admin_user(user_id)

        # 0. 优先检测自然语言【停止/中断/取消】请求
        stop_patterns = [
            r"^(?:停止|取消|中断|别跑了|不要跑了|停下|终止)\s*(?:dsh|任务|执行)?$",
            r"^(?:dsh|deepseek[-_ ]?harness)\s*(?:stop|cancel|停止|取消)$",
            r"^#dsh\s*(?:stop|cancel|停止|取消)$",
        ]
        if any(re.search(pat, text, re.IGNORECASE) for pat in stop_patterns):
            if stream_id in self._active_tasks:
                dsh_session, task_handle = self._active_tasks.pop(stream_id)
                task_handle.cancel()
                if self._acp_client:
                    asyncio.create_task(self._acp_client.cancel_session(dsh_session))
                await self.ctx.send.text("🛑（急忙拉闸）收到停止指令！已为您成功中止当前正在执行的 DSH 任务~ 🐾", stream_id)
            else:
                await self.ctx.send.text("（左右张望）当前会话没有正在运行中的 DSH 任务哦~ 🫧", stream_id)
            return

        # 1. 显式【开启全新会话】指令检测 (#dsh new / 重置dsh会话)
        reset_patterns = [
            r"^(?:重置|清空|新建|开启新)\s*(?:dsh|会话|上下文)$",
            r"^#dsh\s*(?:new|reset|clean)$",
        ]
        if any(re.search(pat, text, re.IGNORECASE) for pat in reset_patterns):
            self._chat_session_history.pop(stream_id, None)
            await self.ctx.send.text("🧹（打扫战场）已为您重置 DSH 会话记忆！下一次任务将从全新干净的沙盒开始~ ✨", stream_id)
            return

        matched_task: Optional[str] = None
        force_new_session = False

        # 方式 A：显式前缀触发 (#dsh ...)
        if text.startswith(prefix):
            matched_task = text[len(prefix):].strip()
            if matched_task.startswith("new "):
                force_new_session = True
                matched_task = matched_task[4:].strip()
            if not matched_task:
                await self.ctx.send.text(
                    f"🐾 DS娘提醒您，指令格式是这样哒：\n{prefix} <你的任务描述/代码需求/排查目标>\n💡 提示：输入 `{prefix} new <任务>` 可强制开新会话",
                    stream_id,
                )
                return

        # 方式 B：全场景泛化自然语言意图感知
        elif cfg.plugin.enable_natural_language:
            m_dsh = re.search(r"(?:^|\s|，|,|。|！|!)(?:请|帮我|让|使用|调用|通过|用)?(?:dsh|deepseek[-_ ]?harness)(?:去|帮我|来|：|:|\s+)?(.+)$", text, re.IGNORECASE)
            if m_dsh:
                candidate = m_dsh.group(1).strip()
                if len(candidate) >= 2 and not candidate.startswith("是什么") and not candidate.startswith("吗"):
                    matched_task = candidate

            if not matched_task:
                engineering_patterns = [
                    r"^(?:按照|参考|根据)\s*skill\s*(.+)$",
                    r"^(?:检查|查看|看下|对比)\s*(?:git\s*版本|代码分支|主分支落后).+$",
                    r"^(?:排查|分析|诊断)\s*(?:/main/log|/main/app|supervisor\s*日志).+$",
                ]
                for ep in engineering_patterns:
                    if re.search(ep, text, re.IGNORECASE):
                        matched_task = text
                        break

        if not matched_task:
            return

        # 防重复触发拦截
        if self._check_and_record_duplicate(stream_id, matched_task, dedupe_window_sec=10.0):
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

        # 方案 B 核心：智能裁决会话
        if force_new_session:
            client = await self._ensure_acp_client()
            chosen_session = await client.create_session()
            session_action = "new"
            self._chat_session_history.setdefault(stream_id, []).append(
                SessionHistoryRecord(chosen_session, matched_task[:80], matched_task, time.time())
            )
        else:
            chosen_session, session_action = await self._resolve_smart_session(stream_id, matched_task)

        # 动态人设提示词
        hint_message = self._get_random_prompt_hint(matched_task, session_action=session_action)
        await self.ctx.send.text(hint_message, stream_id)

        # 异步非阻塞执行任务，防止 HookHandler 30s 熔断
        asyncio.create_task(self._run_and_reply(final_task, stream_id, dsh_session=chosen_session))

    async def _run_and_reply(self, task: str, stream_id: str, dsh_session: Optional[str] = None) -> None:
        """异步执行 DSH 任务并回复群聊/私聊。"""
        cfg = cast(DshBridgeConfig, self.config)

        async def on_progress(progress_text: str, elapsed_sec: int):
            await self.ctx.send.text(progress_text, stream_id)

        try:
            result = await self._execute_dsh_task(task, stream_id=stream_id, dsh_session=dsh_session, progress_cb=on_progress)
            await self._deliver_hybrid_result(result, stream_id, model_name=cfg.model.model)
        except asyncio.CancelledError:
            self.ctx.logger.info("DSH 任务已被用户主动取消: %s", stream_id)
        except Exception as e:
            self.ctx.logger.error("DSH 任务执行异常: %s", e, exc_info=True)
            await self.ctx.send.text(str(e), stream_id)

    async def _deliver_hybrid_result(self, raw_result: str, stream_id: str, model_name: str = "") -> None:
        """方案 C 核心：字数 > 200 或包含代码/表格时，直接渲染 GitHub 深色长图发送。"""
        base_head = random.choice(DEFAULT_SUCCESS_HEADS)
        model_badge = f" [⚡ {model_name}]" if model_name else ""
        success_head = f"{base_head}{model_badge}"

        is_long_content = len(raw_result) > 200 or "```" in raw_result or "\n|" in raw_result

        if is_long_content:
            try:
                img_base64 = render_markdown_to_card_image(raw_result, title=f"DeepSeek Harness 交付报告 ({model_name})")
                if img_base64:
                    await self.ctx.send.text(success_head, stream_id)
                    await self.ctx.send.image(img_base64, stream_id)
                    self.ctx.logger.info("已成功通过图片卡片形式交付结果 (字符数: %d, 模型: %s)", len(raw_result), model_name)
                    return
            except Exception as render_err:
                self.ctx.logger.warning("卡片图片渲染遇到异常，降级为纯文本排版: %s", render_err)

        clean_text = format_markdown_to_clean_text(raw_result)
        await self.ctx.send.text(f"{success_head}\n\n{clean_text}", stream_id)


def create_plugin() -> MaiBotPlugin:
    """Plugin factory export for MaiBot 1.2+."""
    return DshBridgePlugin()
