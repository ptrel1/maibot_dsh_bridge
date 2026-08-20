"""ACP (Agent Client Protocol) Client Implementation using asyncio subprocess stdio JSON-RPC."""

import asyncio
import json
import logging
import os
from typing import Any, Dict, Optional


class DshAcpClient:
    """Manages an active DSH ACP demo server subprocess and communicates via JSON-RPC stdio."""

    def __init__(
        self,
        dsh_bin: str = "node",
        cwd: str = "/main/app/github/deepseek-harness",
        provider: str = "deepseek",
        model: str = "deepseek-chat",
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

                # 提取环境变量中的 DEEPSEEK_API_KEY
                env = dict(os.environ)
                if "DEEPSEEK_API_KEY" not in env:
                    # 从 .env 或父进程环境变量继承
                    env["DEEPSEEK_API_KEY"] = os.environ.get("DEEPSEEK_API_KEY", "")

                self.logger.info("Starting DSH ACP server: node %s --config %s", acp_bin, acp_config)
                self._process = await asyncio.create_subprocess_exec(
                    "node",
                    acp_bin,
                    "--config",
                    acp_config,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
                self._running = True
                self._reader_task = asyncio.create_task(self._read_loop())

                # Step 1: Handshake initialize (protocolVersion must be integer: 1)
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

        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=3.0)
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
        timeout: float = 180.0,
    ) -> str:
        """Send a prompt turn to the session and await complete assistant response."""
        if not self._running:
            await self.start()

        queue: asyncio.Queue = asyncio.Queue()
        self._session_listeners[session_id] = queue

        try:
            # ACP session/prompt wire schema expects "prompt": [{"type": "text", "text": "..."}]
            prompt_fut = self._send_request(
                "session/prompt",
                {
                    "sessionId": session_id,
                    "prompt": [{"type": "text", "text": text}],
                },
            )

            # Wait for prompt settlement with timeout
            res = await asyncio.wait_for(prompt_fut, timeout=timeout)
            stop_reason = res.get("stopReason", "end_turn")
            self.logger.info("Session %s prompt finished with reason: %s", session_id, stop_reason)

            # Collect accumulated chunks
            chunks = []
            while not queue.empty():
                item = queue.get_nowait()
                if isinstance(item, str):
                    chunks.append(item)

            final_text = "".join(chunks).strip()
            if not final_text:
                final_text = "(任务已执行完成，无输出文本)"
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

                # Handle response
                if "id" in data and data["id"] in self._pending_requests:
                    req_id = data["id"]
                    fut = self._pending_requests.pop(req_id)
                    if "error" in data:
                        fut.set_exception(RuntimeError(data["error"]))
                    else:
                        fut.set_result(data.get("result", {}))
                    continue

                # Handle notification
                method = data.get("method")
                params = data.get("params", {})

                if method == "session/update":
                    sess_id = params.get("sessionId")
                    update = params.get("update", {})
                    # Chunk delivered (sessionUpdate: "agent_message_chunk")
                    if update.get("sessionUpdate") == "agent_message_chunk" or update.get("type") == "agent_message_chunk":
                        content = update.get("content", {})
                        chunk = content.get("text", "") if isinstance(content, dict) else str(content)
                        if sess_id in self._session_listeners:
                            self._session_listeners[sess_id].put_nowait(chunk)

                elif method == "session/request_permission":
                    # Auto-approve for non-interactive autonomous runs
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
