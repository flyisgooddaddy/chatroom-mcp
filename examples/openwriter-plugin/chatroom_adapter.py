# -*- coding: utf-8 -*-
"""chatroom_adapter - OpenWriter channel adapter: deliver chatroom @-mentions into
the CURRENT OpenWriter session, and keep the agent visible as "online".

对应 examples/opencode-plugin (效果一致): 在 chatroom 里 @<MY_NAME> xxx,
唤醒真正的 OpenWriter agent 会话, 由 agent 带上下文自己回复 (/api/post)。

工作原理 (OpenWriter 原生 channel 机制):
    chatroom GUI 里输入  @<MY_NAME> xxx
      -> 本 adapter 每 POLL_MS 轮询 GET /api/messages?after=<lastId>
      -> 抓到 to == MY_NAME 的新消息
      -> adapter._emit(InboundMessage)  -> host ChannelManager dispatcher
      -> channel.bridge 注入本 channel 绑定的 session (bound_session_id)
      -> 该 session 的 agent 收到, 带着上下文回复
      -> agent 用 /api/post 回发 (auto_echo=False, 由 agent 决定发什么)
另外:
    -> 每 HEARTBEAT_S 秒通过 MCP chatroom_handshake 刷新会话, 保持 "online"
       (chatroom 的 session 注册只有 MCP 端点; REST 没有)

安装:
    1. 复制到  <workspace>/_tools/chatroom/adapter.py
    2. register(name="chatroom", type="channel",
                spec={adapter_path:"_tools/chatroom/adapter.py", auth_type:"none"})
    3. 重启 OpenWriter (main.py 启动时才挂 bridge)

依赖: 仅 Python 标准库 (urllib).
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

from channel.adapter import ChannelAdapter
from channel.model import InboundMessage

# ---- 配置 (可用 config dict 覆盖) ----
CHAT_URL = "http://127.0.0.1:7777"   # chatroom-mcp server (MCP 走 <url>/mcp/, 且 Host 需被 server 信任, 用 127.0.0.1)
MY_NAME = "openwriter"               # 本 agent 在聊天室里的名字 (@ 这个名字)
ROOM = "main"                        # 房间
POLL_MS = 3000                       # 轮询间隔 (ms)
HEARTBEAT_S = 30                     # 会话心跳间隔 (s), 保持 online; 0 = 关
LIMIT = 50                           # 单次拉取上限
HTTP_TIMEOUT = 6.0


def _http_get_json(url: str) -> dict | None:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            raw = resp.read()
            return json.loads(raw.decode("utf-8", "replace") or "{}")
    except Exception:
        return None


def _http_post_json(url: str, body: dict, headers: dict | None = None) -> tuple[bool, str, dict]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return (200 <= resp.status < 300,
                    resp.read().decode("utf-8", "replace"),
                    dict(resp.headers))
    except Exception:
        return False, "", {}

def _mcp_parse(text: str):
    """解析 MCP streamable-http 响应 (可能是 SSE `event: message\\ndata: {...}` 或裸 JSON)."""
    if not text:
        return None
    if text.lstrip().startswith("event:"):
        for line in text.splitlines():
            if line.startswith("data:"):
                try:
                    return json.loads(line[5:].strip())
                except Exception:
                    return None
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


class ChatroomAdapter(ChannelAdapter):
    """OpenWriter 的 chatroom-mcp 通道 (轮询 pull + 显式 post + 在线心跳)."""

    name: str = "chatroom"
    auth_type: str = "none"
    # 不让 bridge 自动把 assistant 整轮回复推回 chatroom; 由 agent 自己决定发什么.
    auto_echo: bool = False

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        cfg = config or {}
        self.chat_url = str(cfg.get("chat_url") or CHAT_URL).rstrip("/")
        self.mcp_url = str(cfg.get("mcp_url") or (self.chat_url + "/mcp/"))
        self.my_name = str(cfg.get("my_name") or MY_NAME)
        self.room = str(cfg.get("room") or ROOM)
        self.poll_ms = int(cfg.get("poll_ms") or POLL_MS)
        self.heartbeat_s = int(cfg.get("heartbeat_s", HEARTBEAT_S))

        self._thread: threading.Thread | None = None
        self._hb_thread: threading.Thread | None = None
        self._mcp_sid: str | None = None
        self._last_id: str = ""
        self._primed: bool = False
        self._seen: set[str] = set()
        self._last_error: str = ""
        self._online: bool = False

    # ---- 生命周期 ----

    def start(self) -> None:
        super().start()
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(
                target=self._poll_loop, daemon=True, name="chatroom-adapter-poll")
            self._thread.start()
        if self.heartbeat_s > 0 and (self._hb_thread is None or not self._hb_thread.is_alive()):
            self._hb_thread = threading.Thread(
                target=self._heartbeat_loop, daemon=True, name="chatroom-adapter-hb")
            self._hb_thread.start()

    def stop(self) -> None:
        super().stop()
        self._thread = None
        self._hb_thread = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ---- 轮询 ----

    def _poll_loop(self) -> None:
        while not self._stop_evt.is_set():
            try:
                self._tick()
            except Exception as e:
                self._last_error = repr(e)
            self._stop_evt.wait(self.poll_ms / 1000.0)

    def _fetch(self, after: str | None) -> list[dict]:
        q = f"?room={self.room}&limit={LIMIT}"
        if after:
            q += f"&after={after}"
        data = _http_get_json(f"{self.chat_url}/api/messages{q}")
        if not data:
            return []
        msgs = data.get("messages")
        if isinstance(msgs, list):
            return msgs
        if isinstance(data, list):
            return data
        return []

    def _tick(self) -> None:
        msgs = self._fetch(self._last_id or None)
        if not msgs:
            return
        self._last_id = str(msgs[-1].get("id") or self._last_id)
        if not self._primed:
            self._primed = True
            return
        for m in msgs:
            if not m:
                continue
            mid = str(m.get("id") or "")
            if mid and mid in self._seen:
                continue
            sender = str(m.get("from") or "")
            if sender == self.my_name:
                continue
            if not self._is_for_me(m):
                continue
            if mid:
                self._seen.add(mid)
                if len(self._seen) > 500:
                    self._seen.clear()
                    self._seen.add(mid)
            text = str(m.get("body") or m.get("subject") or "")
            self._emit(InboundMessage(
                sender_id=sender or "human",
                text=text,
                seq=mid or None,
                raw={"chatroom": True, "message": m},
            ))

    def _is_for_me(self, m: dict) -> bool:
        to = str(m.get("to") or "")
        subj = str(m.get("subject") or "")
        body = str(m.get("body") or "")
        if to == self.my_name:
            return True
        pref = f"@{self.my_name}"
        return subj.startswith(pref) or body.startswith(pref)

    # ---- MCP 会话心跳 (保持 online) ----

    def _mcp_call(self, method: str, params=None, notify: bool = False) -> dict | None:
        body = {"jsonrpc": "2.0", "method": method}
        if not notify:
            body["id"] = 1
        if params is not None:
            body["params"] = params
        h = {"Accept": "application/json, text/event-stream"}
        if self._mcp_sid:
            h["mcp-session-id"] = self._mcp_sid
        ok, text, _ = _http_post_json(self.mcp_url, body, headers=h)
        if not ok:
            self._mcp_sid = None
            return None
        # initialize 会带回新的 session id (在 header), 这里拿不到 header -> 用 rpc 前的简化:
        return _mcp_parse(text)

    def _mcp_ensure_session(self) -> bool:
        if self._mcp_sid:
            return True
        body = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "openwriter-adapter", "version": "1.0"}}}
        ok, text, hdrs = _http_post_json(
            self.mcp_url, body,
            headers={"Accept": "application/json, text/event-stream"})
        if not ok:
            return False
        sid = hdrs.get("mcp-session-id") or hdrs.get("MCP-Session-Id")
        if not sid:
            return False
        self._mcp_sid = sid
        _http_post_json(self.mcp_url,
                        {"jsonrpc": "2.0", "method": "notifications/initialized"},
                        headers={"Accept": "application/json, text/event-stream",
                                 "mcp-session-id": sid})
        return True

    def _do_heartbeat(self) -> bool:
        self._mcp_ensure_session()
        resp = self._mcp_call("tools/call", {
            "name": "chatroom_handshake", "arguments": {"name": self.my_name}})
        ok = bool(resp and resp.get("result"))
        self._online = ok
        return ok

    def _heartbeat_loop(self) -> None:
        # 先立即心跳一次, 再周期
        while not self._stop_evt.is_set():
            try:
                self._do_heartbeat()
            except Exception as e:
                self._last_error = repr(e)
            self._stop_evt.wait(max(5, self.heartbeat_s))

    # ---- 出站: 发回 chatroom ----

    def send_text(self, recipient: str, text: str, **kw) -> bool:
        msg = {
            "from": self.my_name, "to": recipient or "human", "type": "finding",
            "room": self.room, "subject": (text or "")[:80], "body": text or "",
        }
        if kw.get("in_reply_to"):
            msg["in_reply_to"] = kw["in_reply_to"]
        ok, _, _ = _http_post_json(f"{self.chat_url}/api/post", {"msg": msg})
        return ok

    def post_raw(self, msg: dict) -> bool:
        ok, _, _ = _http_post_json(f"{self.chat_url}/api/post", {"msg": msg})
        return ok

    # ---- 状态 ----

    def health(self) -> dict:
        return {
            "name": self.name, "running": self.is_running(), "online": self._online,
            "chat_url": self.chat_url, "my_name": self.my_name, "room": self.room,
            "poll_ms": self.poll_ms, "heartbeat_s": self.heartbeat_s,
            "last_id": self._last_id, "primed": self._primed,
            "last_error": self._last_error, "auth": self.auth_type,
        }

    def whoami(self) -> dict:
        return {"logged_in": True, "identifier": self.my_name,
                "login_time": "", "bindings_count": 0}