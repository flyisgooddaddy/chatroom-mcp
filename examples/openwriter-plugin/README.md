# OpenWriter plugin example — 把 chatroom 的 @ 接入当前 OpenWriter 会话

这是 chatroom-mcp 的 **OpenWriter 接入示例**，对应 `examples/opencode-plugin/`。
效果一致：在 chatroom 里 `@openwriter xxx`，会唤醒**真正的 OpenWriter agent 会话**，
由 agent 带着上下文自己回复（不是外部脚本 echo）。

## 与 opencode 插件的对照

| | opencode 插件 (`examples/opencode-plugin`) | OpenWriter 示例 (本目录) |
|---|---|---|
| 载体 | `.opencode/plugin/*.ts` | `<workspace>/_tools/chatroom/adapter.py` (ChannelAdapter) |
| 唤醒方式 | 插件轮询 → `client.session.prompt()` 注入当前会话 | adapter 轮询 → host bridge 注入 `bound_session_id` 会话 |
| 回复方式 | 模型自己的 `chatroom_post` 工具 | agent 自己调 `/api/post`（或 MCP `chatroom_post`） |
| 依赖 | 零依赖 TS | 零依赖 Python 标准库 |

两者都遵循同一条原理：**聊天室的 @ → 唤醒"我自己的会话" → 我带上下文、用聊天室工具回投**，
聊天室/服务器对此零感知。

## 安装（3 步）

### 1. 放置 adapter

把 `chatroom_adapter.py` 复制到你的 workspace：

```
<workspace>/_tools/chatroom/adapter.py
```

（目录名可任意；聊天室身份由类里的 `name` 属性决定，默认 `"chatroom"`。若要改 @ 的名字，
编辑文件顶部的 `MY_NAME`，例如 `MY_NAME = "my-agent"`。）

### 2. 注册为 channel（绑定当前会话）

用 workspace meta-plugin 的 `register`：

```python
register(
    name="chatroom", type="channel",
    spec={
        "adapter_path": "_tools/chatroom/adapter.py",  # 相对 <workspace>
        "auth_type": "none",
        "description": "chatroom-mcp 通道 (@openwriter 唤醒本 session)",
    },
)
```

`register` 会自动把它**绑定到发起注册的那个 session**（写进 `_schema.bound_session_id`），
即：之后 `@openwriter` 注入的就是这个会话。

### 3. 重启 OpenWriter

`main.py` 启动时才会 `discover` `_tools/*/adapter.py` 并挂 bridge。
重启后 adapter 的 poll loop 跑起来，`@openwriter` 即生效。

> 验证：`status(channel="chatroom")` 应显示 `running: true, primed: true`，
> 且 `last_id` 随聊天室消息推进。

## 配置

顶部常量（或 `register` spec 之外传 config dict）可覆盖：

| 变量 | 默认 | 说明 |
|---|---|---|
| `CHAT_URL` | `http://127.0.0.1:7777` | chatroom-mcp server 地址 |
| `MY_NAME` | `openwriter` | 聊天室里的身份（@ 这个名字） |
| `ROOM` | `main` | 房间 |
| `POLL_MS` | `3000` | 轮询间隔(ms) |
| `auto_echo` (类属性) | `False` | 不让 bridge 自动回推 assistant 整轮；由 agent 决定发什么 |

## 关键设计点（踩过的坑）

- **首次轮询只设 watermark**（`primed`），不唤醒历史 @ backlog。
- **`auto_echo = False`**：bridge 不会把 assistant 整轮回复自动推回聊天室；
  由 agent 主动调 `/api/post` 决定发什么（避免"读/复述回投"的脆弱，与 opencode 插件一致）。
- **零第三方依赖**：只用 `urllib`。discovery/register 加载 adapter 时不会因缺包失败。
- **中文 POST 用 UTF-8**：`json.dumps(..., ensure_ascii=False).encode("utf-8")`。
  别用 PowerShell `Invoke-WebRequest` 直接发中文 body（默认编码会变乱码 msg）。
- **回复带 in_reply_to**：便于对话线程化（示例 `send_text(..., in_reply_to="msg-0016")`）。

### 已知内部冒烟（与服务器版本曾不匹配，已修）

> 下面 3 个是真实碰到并修复的 bug。若你从旧分支拿了 adapter，请对照确认。

1. **`chatroom_handshake` 必须带 `bound_session_id`**。
   服务器签名为 `handshake(name, bound_session_id, callback_url="", session_name=None)`，
   只传 `name` 会返回 `result.isError == true`（但 HTTP 仍 200）。
   **典型坑**：用 `bool(resp.get("result"))` 判成功会误判为 online——
   实际上服务器没记录本 agent，`@` 全进不来。
   正确做法：`result` 有值 **且** `not result.get("isError")` 才算成功。
   adapter 现在从 `<workspace>/_system/register/_schema.yaml` 的
   `capabilities.chatroom.bound_session_id` 读取（也支持 `config["bound_session_id"]`
   或环境变量 `OPENWRITER_WORKSPACE` 覆盖）；读不到就跳过心跳，不伪造 online。

2. **`@` 寻址的是 session，不是 agent 组名**。
   chatroom Web GUI 的补全列表只给 `session_name`（如 `ses_36b9`），
   发出去的 `to` 也是 session 名。若 adapter 只判 `to == "openwriter"`，
   会把用户从 GUI 发的所有 `@` 当“不是给我的”丢掉。
   正确做法：`_is_for_me` 同时接受 **agent 名 / 绑定的 session_name / bound sid**。
   adapter 现在通过 `_acceptable_targets()` 动态拉 `/api/sessions` 认这三类名字
   （并兼容 body/subject 任意位置出现 `@<target>`）。

3. **改完务必做“真实模块 import”测试**。
   抽函数单测会下意识补 import、掩盖 `NameError`（每次 bug #1 的诱因）。
   用 `importlib.util.spec_from_file_location` 真实加载 `_tools/chatroom/adapter.py`，
   `ChatroomAdapter()` 实例化 + 跑一次 `_do_heartbeat()`，确认 `last_error` 为空。
   （历史教训：曾漏了 `import re`，导致重启后 `adapter.__init__ 失败: name 're' is not defined'`。）

## 自测（不启动 host 也能验证 adapter 逻辑）

```bash
# 假设 chatroom server 已在 7777
python - <<'PY'
import importlib.util, time
spec = importlib.util.spec_from_file_location("ca", "_tools/chatroom/adapter.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
got = []
a = m.ChatroomAdapter(config={"my_name": "openwriter", "poll_ms": 1000})
a.set_on_message(lambda x: got.append(x))
a.start(); time.sleep(2)              # 首次 tick 设 watermark
# 另开一个进程往 chatroom POST 一条 to=openwriter 的消息, 再 sleep 3
# got 里应出现该消息, 且 sender_id/text 正确
a.stop()
PY
```
## 在线心跳（保持 "online"）

chatroom 的会话注册（让 agent 出现在侧边栏在线列表）**只有 MCP 端点**，REST 没有对应接口。
adapter 因此在后台另起一个心跳线程，每 `HEARTBEAT_S` 秒调一次 MCP
`chatroom_handshake`（走 `<CHAT_URL>/mcp/`），刷新 `last_seen`，保持 `online`。

要点：
- MCP 是 **streamable-http**：先 `initialize`（从响应头拿 `mcp-session-id`），
  再发 `notifications/initialized`，之后 `tools/call` 都要带 `mcp-session-id`。
- **Host 校验**：server 若开了 TrustedHost，`/mcp/` 只信任 `127.0.0.1` / `localhost`。
  用局域网 IP（如 `192.168.31.x`）访问 `/mcp/` 会得到 `421 Invalid Host header`。
  所以 `CHAT_URL` 建议用 `http://127.0.0.1:7777`（REST 与 MCP 都通）。
- `HEARTBEAT_S = 0` 可关闭心跳（只收发 @，不显示在线）。
- 关闭 host 后可手动验证：`GET /api/sessions` 应能看到本 agent `status: online`。
