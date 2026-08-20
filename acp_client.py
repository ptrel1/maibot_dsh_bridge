"""ACP (Agent Client Protocol) Client Implementation using asyncio subprocess stdio JSON-RPC."""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional


def resolve_dsh_home() -> str:
    """自动动态探测并定位有效的 DSH_HOME 目录，杜绝硬编码。
    
    优先级：
    1. 现有环境变量 DSH_HOME；
    2. 当前用户 HOME 目录 (~/.dsh)；
    3. 系统常见用户目录 (/home/*/.dsh) 中含有 settings.yaml 的有效目录；
    4. /root/.dsh。
    """
    if os.environ.get("DSH_HOME"):
        cand = Path(os.environ["DSH_HOME"])
        if cand.exists():
            return str(cand)

    # 检查当前用户 HOME
    home = Path.home() / ".dsh"
    if (home / "settings.yaml").exists():
        return str(home)

    # 若为 root 运行，自动扫描 /home 下各用户的 .dsh 真实有效配置
    home_parent = Path("/home")
    if home_parent.exists():
        try:
            for udir in home_parent.iterdir():
                if udir.is_dir():
                    cand = udir / ".dsh"
                    if (cand / "settings.yaml").exists():
                        return str(cand)
        except Exception:
            pass

    # 兜底
    if home.exists():
        return str(home)
    return str(Path.home() / ".dsh")


class DshAcpClient:
    """Manages an active DSH ACP demo server subprocess and communicates via JSON-RPC stdio."""

    def __init__(
        self,
        dsh_bin: str = "node",
        cwd: str = "/main/app/github/deepseek-harness",
        provider: str = "maiapi2",
        model: str = "gemini-3.7-flash-tiered",
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.dsh_bin = dsh_bin
        self.default_cwd = cwd
        self.provider = provider
        self.model = model
        self.logger = logger or logging.getLogger("DshAcpClient")

        self._process: Optional[asyncio.subprocess.Process] = None
        self._req_id: int = 0
        self._pending_requests: Dict[int, asyncio.Future] = {}
        self._session_listeners: Dict[str, asyncio.Queue] = {}
        self._reader_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._running = False

    async def start(self) -> bool:
        """Start the DSH ACP server subprocess and perform initialize handshake."""
        async with self._lock:
            if self._running and self._process and self._process.returncode is None:
                return True

            try:
                acp_bin = "/main/app/github/deepseek-harness/packages/examples/acp-demo/lib/bin.js"
                acp_config = "/main/app/github/deepseek-harness/examples/acp-agent/cordis.yml"

                env = dict(os.environ)
                # 动态自适应探测 DSH_HOME
                dsh_home_path = resolve_dsh_home()
                env["DSH_HOME"] = dsh_home_path
                env["DSH_PERMISSION_MODE"] = "danger-full-access"
                env["DSH_MODEL_PROVIDER"] = self.provider
                env["DSH_MODEL_NAME"] = self.model

                if "DEEPSEEK_API_KEY" not in env or not env["DEEPSEEK_API_KEY"]:
                    env["DEEPSEEK_API_KEY"] = "sk-4008ffef74d94c36a980393c7b856da6"

                self.logger.info(
                    "Starting DSH ACP server (%s/%s, DSH_HOME=%s): node %s",
                    self.provider,
                    self.model,
                    dsh_home_path,
                    acp_bin,
                )
                self._process = await asyncio.create_subprocess_exec(
                    "node",
                    acp_bin,
                    "--config",
                    acp_config,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                    preexec_fn=os.setsid,
                )
                self._running = True
                self._reader_task = asyncio.create_task(self._read_loop())
                self._stderr_task = asyncio.create_task(self._stderr_loop())

                # Step 1: Handshake initialize
                init_res = await self._send_request(
                    "initialize",
                    {
                        "protocolVersion": 1,
                        "clientInfo": {"name": "maibot-dsh-bridge", "version": "0.1.0"},
                        "capabilities": {},
                    },
                )
                self.logger.info("DSH ACP server initialized successfully: %s", init_res)
                return True
            except Exception as e:
                self.logger.error("Failed to start DSH ACP server: %s", e, exc_info=True)
                await self.stop()
                return False

    async def stop(self) -> None:
        """Stop subprocess and cancel all pending futures."""
        self._running = False
        if self._reader_task:
            self._reader_task.cancel()
            self._reader_task = None
        if self._stderr_task:
            self._stderr_task.cancel()
            self._stderr_task = None

        if self._process:
            try:
                if hasattr(os, "killpg"):
                    os.killpg(os.getpgid(self._process.pid), 9)
                else:
                    self._process.kill()
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None

        for fut in self._pending_requests.values():
            if not fut.done():
                fut.cancel()
        self._pending_requests.clear()

    async def cancel_session(self, session_id: str) -> None:
        """Send explicit session/cancel to abort in-flight work."""
        try:
            if self._running and self._process and self._process.stdin:
                self.logger.info("Cancelling in-flight DSH session: %s", session_id)
                await self._send_request("session/cancel", {"sessionId": session_id})
        except Exception as e:
            self.logger.warning("Failed to cleanly cancel session %s: %s", session_id, e)

    async def create_session(self, cwd: Optional[str] = None) -> str:
        """Create a new session in DSH ACP."""
        if not self._running:
            await self.start()

        target_cwd = cwd or self.default_cwd
        params = {
            "cwd": target_cwd,
            "mcpServers": [],
            "additionalDirectories": [],
        }
        res = await self._send_request("session/new", params)
        session_id = res.get("sessionId")
        if not session_id:
            raise RuntimeError(f"Failed to obtain sessionId from ACP: {res}")
        return session_id

    async def prompt(
        self,
        session_id: str,
        text: str,
        timeout: float = 1800.0,
    ) -> str:
        """Send a prompt turn to the session and await complete assistant response."""
        if not self._running:
            await self.start()

        queue: asyncio.Queue = asyncio.Queue()
        self._session_listeners[session_id] = queue

        try:
            prompt_fut = self._send_request(
                "session/prompt",
                {
                    "sessionId": session_id,
                    "prompt": [{"type": "text", "text": text}],
                },
            )

            try:
                res = await asyncio.wait_for(prompt_fut, timeout=timeout)
                stop_reason = res.get("stopReason", "end_turn")
                self.logger.info("Session %s prompt finished with reason: %s", session_id, stop_reason)
            except asyncio.TimeoutError:
                self.logger.warning("Session %s prompt exceeded %s seconds timeout. Cancelling...", session_id, timeout)
                asyncio.create_task(self.cancel_session(session_id))
                raise TimeoutError(f"DSH 智能体执行超时（已超过 {int(timeout)} 秒）。已为您自动中止后台任务。")

            chunks = []
            while not queue.empty():
                item = queue.get_nowait()
                if isinstance(item, str):
                    chunks.append(item)

            final_text = "".join(chunks).strip()
            if not final_text:
                final_text = "(任务已执行完成，无文本输出)"
            return final_text
        finally:
            self._session_listeners.pop(session_id, None)

    async def _send_request(self, method: str, params: Dict[str, Any]) -> Any:
        if not self._process or not self._process.stdin:
            raise RuntimeError("ACP subprocess is not running")

        self._req_id += 1
        req_id = self._req_id
        fut = asyncio.get_running_loop().create_future()
        self._pending_requests[req_id] = fut

        payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        self._process.stdin.write(line.encode("utf-8"))
        await self._process.stdin.drain()

        return await fut

    async def _stderr_loop(self) -> None:
        """Background loop reading stderr diagnostics from DSH ACP process."""
        if not self._process or not self._process.stderr:
            return
        while self._running and self._process:
            try:
                line = await self._process.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    self.logger.warning("[DSH-ACP-STDERR] %s", text)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error("Error in ACP stderr read loop: %s", e)
                break

    async def _read_loop(self) -> None:
        """Background loop reading stdout from DSH ACP process."""
        if not self._process or not self._process.stdout:
            return

        while self._running and self._process:
            try:
                line = await self._process.stdout.readline()
                if not line:
                    break

                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue

                try:
                    data = json.loads(text)
                except Exception:
                    continue

                if "id" in data and data["id"] in self._pending_requests:
                    req_id = data["id"]
                    fut = self._pending_requests.pop(req_id)
                    if "error" in data:
                        fut.set_exception(RuntimeError(data["error"]))
                    else:
                        fut.set_result(data.get("result", {}))
                    continue

                method = data.get("method")
                params = data.get("params", {})

                if method == "session/update":
                    sess_id = params.get("sessionId")
                    update = params.get("update", {})
                    if update.get("sessionUpdate") == "agent_message_chunk" or update.get("type") == "agent_message_chunk":
                        content = update.get("content", {})
                        chunk = content.get("text", "") if isinstance(content, dict) else str(content)
                        if sess_id in self._session_listeners:
                            self._session_listeners[sess_id].put_nowait(chunk)

                elif method == "session/request_permission":
                    req_id = data.get("id")
                    if req_id is not None and self._process.stdin:
                        reply = {"jsonrpc": "2.0", "id": req_id, "result": {"outcome": "accepted"}}
                        self._process.stdin.write((json.dumps(reply) + "\n").encode("utf-8"))
                        await self._process.stdin.drain()

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error("Error in ACP stdout read loop: %s", e)
                await asyncio.sleep(0.1)
