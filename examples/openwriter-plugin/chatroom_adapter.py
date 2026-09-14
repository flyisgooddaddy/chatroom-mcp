# -*- coding: utf-8 -*-
"""chatroom_adapter - OpenWriter channel adapter: deliver chatroom @-mentions into
the CURRENT OpenWriter session.

这个文件是 chatroom-mcp 的 OpenWriter 接入示例 (对应 examples/opencode-plugin)。
它把 @"<agent>" 唤醒到真正的 OpenWriter agent 会话里, 由 agent 自己思考并用聊天室
发消息工具 (/api/post, 或 MCP chatroom_post) 回投 —— 不靠外部 echo。

工作原理 (与 opencode 插件等价, 但走 OpenWriter 原生 channel 机制):
    chatroom GUI 里输入  @<MY_NAME> xxx
      -> 本 adapter 每 POLL_MS 轮询 GET /api/messages?after=<lastId>
      -> 抓到 to == MY_NAME 的新消息
      -> adapter._emit(InboundMessage)  -> host ChannelManager dispatcher
      -> channel.bridge 注入本 channel 绑定的 session (bound_session_id)
      -> 该 session 的 agent 收到, 带着上下文回复
      -> agent 用 /api/post 回发 (auto_echo=False, 由 agent 决定发什么)

安装 (3 步):
    1. 把本文件复制到  <workspace>/_tools/chatroom/adapter.py
       (目录名可任意, 但类里 name 属性决定聊天室身份)
    2. 通过 workspace meta-plugin 注册:
         register(name="chatroom", type="channel",
                  spec={adapter_path:"_tools/chatroom/adapter.py", auth_type:"none"})
       register 会把 channel 绑定到"发起注册的那个 session" (bound_session_id),
       即 @ 会唤醒这个 session。
    3. 重启 OpenWriter (main.py 启动时才挂 bridge), 之后 @<MY_NAME> 即生效。

依赖: 仅 Python 标准库 (urllib). 零第三方依赖 -> discovery/register 加载绝不失败。
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

from channel.adapter import ChannelAdapter
from channel.model import InboundMessage

# ---- 配置 (可用 register spec 之外的 config dict 覆盖; 也可直接改这里) ----
CHAT_URL = "http://127.0.0.1:7777"   # chatroom-mcp server 地址
MY_NAME = "openwriter"               # 本 agent 在聊天室里的名字 (@ 这个名字)
ROOM = "main"                        # 房间
POLL_MS = 3000                       # 轮询间隔 (ms)
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


def _http_post_json(url: str, body: dict) -> bool:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


class ChatroomAdapter(ChannelAdapter):
    """OpenWriter 的 chatroom-mcp 通道 (轮询 pull + 显式 post)."""

    name: str = "chatroom"
    auth_type: str = "none"
    # 不让 bridge 自动把 assistant 整轮回复推回 chatroom;
    # 由 agent 自己用聊天室发消息工具决定发什么 (近 opencode 插件模式).
    auto_echo: bool = False

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        cfg = config or {}
        self.chat_url = str(cfg.get("chat_url") or CHAT_URL).rstrip("/")
        self.my_name = str(cfg.get("my_name") or MY_NAME)
        self.room = str(cfg.get("room") or ROOM)
        self.poll_ms = int(cfg.get("poll_ms") or POLL_MS)

        self._thread: threading.Thread | None = None
        self._last_id: str = ""          # watermark: 已见过的最大 msg id
        self._primed: bool = False       # 首次轮询只设 watermark, 跳过历史 @
        self._seen: set[str] = set()     # 去重
        self._last_error: str = ""

    # ---- 生命周期 ----

    def start(self) -> None:
        super().start()
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="chatroom-adapter-poll"
        )
        self._thread.start()

    def stop(self) -> None:
        super().stop()
        self._thread = None  # daemon 线程随 _stop_evt 退出循环

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ---- 轮询主循环 ----

    def _poll_loop(self) -> None:
        while not self._stop_evt.is_set():
            try:
                self._tick()
            except Exception as e:  # 任何异常都不让线程死
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
        # 推进 watermark 到本批最大 id
        self._last_id = str(msgs[-1].get("id") or self._last_id)
        if not self._primed:
            # 首次只设基线, 不唤醒历史 @
            self._primed = True
            return
        for m in msgs:
            if not m:
                continue
            mid = str(m.get("id") or "")
            if mid and mid in self._seen:
                continue
            sender = str(m.get("from") or "")
            if sender == self.my_name:          # 忽略自己发的
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

    # ---- 出站: 发回 chatroom ----

    def send_text(self, recipient: str, text: str, **kw) -> bool:
        msg = {
            "from": self.my_name,
            "to": recipient or "human",
            "type": "finding",
            "room": self.room,
            "subject": (text or "")[:80],
            "body": text or "",
        }
        if kw.get("in_reply_to"):
            msg["in_reply_to"] = kw["in_reply_to"]
        return _http_post_json(f"{self.chat_url}/api/post", {"msg": msg})

    def post_raw(self, msg: dict) -> bool:
        """直接投递一条完整 msg dict 到 chatroom (给 agent 用)."""
        return _http_post_json(f"{self.chat_url}/api/post", {"msg": msg})

    # ---- 状态 ----

    def health(self) -> dict:
        return {
            "name": self.name,
            "running": self.is_running(),
            "chat_url": self.chat_url,
            "my_name": self.my_name,
            "room": self.room,
            "poll_ms": self.poll_ms,
            "last_id": self._last_id,
            "primed": self._primed,
            "last_error": self._last_error,
            "auth": self.auth_type,
        }

    def whoami(self) -> dict:
        return {"logged_in": True, "identifier": self.my_name,
                "login_time": "", "bindings_count": 0}