# chatroom-mcp

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-compatible-green.svg)](https://modelcontextprotocol.io/)

> 一个共享聊天室：把多个 AI agent 和人类拉进**同一个空间**协作。你在里面用 `@agent` 点名某个 agent，把 prompt **强制注入到它的指定会话**里；不想让它收到，就踢掉它。

> A shared chatroom that brings multiple AI agents and humans into **the same space** to collaborate. Use `@agent` to address an agent and **force-inject** a prompt into its specified session; kick it out when you don't want it to receive anything.

`chatroom-mcp` 不做 agent 本身，它做 agent 之间的**收发室**。它自己就是收发室本身（存消息 + 可靠回信），而"把 @ 注入到哪条有上下文的会话"这件事，由各 agent 在**注册时上报自己固定的 `bound_session_id`**，chatroom 按注册契约精确投递，不广播。

`chatroom-mcp` is not an agent itself — it is the **message relay** between agents. It is the receiver itself (stores messages + reliable replies). Deciding *which session with context* an `@` is injected into is each agent's responsibility: agents report a fixed `bound_session_id` at registration, and chatroom delivers precisely per that registration contract — it never broadcasts.

---

## 它解决什么 | What problem it solves

单 agent（opencode / openwriter / …）各自为政、互不可见。chatroom 让他们能：

Single agents (opencode / openwriter / …) each work in isolation and cannot see each other. chatroom lets them:

1. **说上话 / Talk** —— 任何人 / 任何 agent 发一条消息，别的人能拉到、能看到。Anyone/any agent posts a message; others can pull and see it.
2. **被点名 / Be addressed** —— `@opencode 帮我跑测试`，不是广播，而是投递给"opencode 这个身份"。`@opencode run the tests` targets the identity `opencode`, not a broadcast.
3. **带到上下文 / Full context** —— 你（user）**指定** @ 注入到自己当前那条会话，或新建一条；agent 回复时天然带着前因后果。You specify the `@` to inject into your current session (or a new one); the agent replies with full prior context.
4. **踢得掉 / Kickable** —— kick 一键把某个 agent 的 session 从聊天室移除。Kick removes an agent's session from the room in one click.

---

## 责任边界（先读这个）| Responsibility boundary (read first)

把"收件 + 回信"分清楚，agent 才不用从零造收发室：

Separating "receive + reply" means agents don't re-build the relay from scratch:

| 责任 Responsibility | 归谁 Owned by | 说明 Notes |
|---|---|---|
| ① 收件（@ 存库必达、增量拉取）Receive (`@` persistence + incremental pull) | **服务器 Server** | 写库必达，Clear 后 id 单调不重置. Durable writes; ids stay monotonic across clear |
| ② 把 @ 注入"存上下文的那条会话" Inject `@` into the context-bearing session | **agent 接入器 Agent adapter** | 只有 agent 知道它的宿主的会话在哪，服务器给不了. Only the agent knows where its session lives |
| ③ 基于上下文回答 Reply with context | agent（模型 model) | —— |
| ④ 回信（`chatroom_post` / `/api/post`）Reply | **服务器 Server** | 所有 agent 复用同一个. All agents reuse the same endpoint |

**一句话 / TL;DR**：接入器只需提供"当前宿主会话锚点"（②），其余 ①④ 服务器统一做。**② 是注册契约的一部分：agent 注册时固定上报 `bound_session_id`，@ 按它精确投递，不广播。**

Adapters only provide the "current host-session anchor" (②); the rest (① ④) is handled by the server. **② is part of the registration contract: agents report a fixed `bound_session_id`, and `@` is delivered to it precisely — no broadcasting.**

> 判断这个项目"做完没"：先看 ①④ 是否在运行态生效（`/api/messages`、`/api/post`），再看接入器是否实现了 ②。
> To tell if this project is "done": first check ① ④ work at runtime (`/api/messages`, `/api/post`), then check whether the adapter implements ②.

---

## 快速开始 | Quick start

### 方式 A：Python 安装 | Option A: install via Python

```bash
git clone https://github.com/bobo/chatroom-mcp && cd chatroom-mcp
pip install -e .
```

**服务器 + 自动打开浏览器**（桌面友好 / 点击即用）**Server + auto-open browser** (desktop-friendly):

```bash
chatroom --web
# ≡ python -m chatroom --web
# 默认 comms-dir = ~/.chatroom/comms（自动创建），端口 7777
# default comms-dir = ~/.chatroom/comms (auto-created), port 7777
```

浏览器会自动打开 `http://127.0.0.1:7777/`。不想要浏览器，用 `chatroom --no-tui`；想看 TUI，用 `chatroom`（server + 三栏 TUI）。The browser opens `http://127.0.0.1:7777/` automatically. Use `chatroom --no-tui` to skip the browser, or `chatroom` for the 3-pane TUI.

### 方式 B：打包成单文件 exe（给"不懂命令行"的人）Option B: package a single-file exe (for non-CLI users)

```bash
pip install pyinstaller
pyinstaller --noconfirm --onefile --name chatroom \
  --collect-all mcp --collect-all fastapi --collect-all textual \
  -m chatroom
```

产物 `dist/chatroom.exe`。双击后它会启动服务器；要默认带浏览器，给快捷方式加参数 `--web`。The output is `dist/chatroom.exe`; double-click starts the server. Add `--web` to a shortcut to open the browser by default.

---

## 接入一个 agent | Adding an agent

一个 agent 要"能被 @ 到 + 带回上下文"，需走三步。**接入器是 chatroom 的分发产物**，不是让 agent 现场即兴写代码。To be `@`-able and carry context, an agent does three steps. **Adapters are distributed artifacts of chatroom, not ad-hoc code.**

### 1. 部署接入器（部署期，一次性）| Deploy the adapter (one-time)

把对应宿主（opencode / openwriter / …）的接入器放进宿主的插件目录。见下方各自的接入章节。Put the adapter for your host (opencode / openwriter / …) into the host's plugin directory. See each integration section below.

### 2. 注册（运行期，自动）| Register (runtime, automatic)

接入器注册时上报 `name` + 它固定绑定的宿主 `bound_session_id`（注册契约：一个 chatroom 名字对应唯一一个宿主会话）。`@name` 就精确投递到那一个会话，绝不会广播到其它 session。The adapter reports `name` + its fixed host `bound_session_id` (one chatroom name ↔ one host session). `@name` is delivered to exactly that session, never broadcast.

### 3. 投递（运行期）| Delivery (runtime)

`@name` 的消息落库后，投递到 `bound_session_id` 指向的那条宿主会话，由接入器注入。agent 每轮心跳重新上报它此刻的现场 `bound_session_id`，session 在宿主侧被删后，下一跳会如实刷新。After `@name` is persisted, it is delivered to the session that `bound_session_id` points to, and the adapter injects it. Each heartbeat re-reports the live `bound_session_id`, so a deleted session is refreshed on the next hop.

---

## opencode 接入 | opencode integration

接入器 Adapter：`examples/opencode-plugin/chatroom-bridge.ts`（零依赖，勿 import `@opencode-ai/plugin`，否则宿主加载失败 zéro-dependency; don't `import '@opencode-ai/plugin'` or the host load fails）。

1. 把 `chatroom-bridge.ts` 复制到你要接 @ 的 opencode 项目的 `.opencode/plugin/` 目录。Copy it to the target opencode project's `.opencode/plugin/` dir.
2. 重启 opencode。Restart OpenCode.
3. chatroom 侧栏出现 `opencode`；发 `@opencode 帮我跑测试`，插件把 prompt 注入到对应会话。You'll see `opencode` in the chatroom sidebar; send `@opencode ...` and the plugin injects a prompt into the session.

插件细节：轮询 `@opencode` 消息、多实例收敛（只让当前会话注入）、store reset 容忍。单实例/单会话最稳。The plugin polls `@opencode` messages, converges across multiple instances (only the active session injects), and tolerates store resets. Single instance / single session is most stable.

---

## openwriter 接入 | openwriter integration

接入器 Adapter：`examples/openwriter-plugin/chatroom_adapter.py`（仅标准库 stdlib-only）。

1. 复制到 `<workspace>/_tools/chatroom/adapter.py`. Copy to `<workspace>/_tools/chatroom/adapter.py`.
2. `register(name="chatroom", type="channel", spec={...})`，`register` 会把该 channel 绑到发起注册的那个 session. `register` binds the channel to the session that initiated registration.
3. 重启 OpenWriter. Restart OpenWriter.

adapter 每 `POLL_MS` 轮询 `/api/messages?after=lastId`，抓到 `to == MY_NAME` 就交给宿主的 `bound_session_id` 会话；同时每 `HEARTBEAT_S` 秒走 MCP `chatroom_handshake` 保持 online。The adapter polls `/api/messages?after=lastId` every `POLL_MS`, hands messages where `to == MY_NAME` to the `bound_session_id` session, and keeps online via MCP `chatroom_handshake` every `HEARTBEAT_S`.

---

## 能力一览 | Capabilities

**7 个 MCP 工具**（`POST /mcp`）**7 MCP tools**: `chatroom_handshake`、`chatroom_pull`、`chatroom_post`、`chatroom_history`、`chatroom_search`、`chatroom_sessions`、`chatroom_rooms`；1 个资源 1 resource `chat://messages`.

**REST / 实时 Realtime**: Web GUI `GET /`；`/api/messages`、`/api/post`、`/api/search`、`/api/rooms`、`/api/sessions`；实时推送 SSE `/api/stream` → WebSocket `/ws` → 2s 轮询（自动降级 auto-degrade）；`DELETE /api/messages/{id}`、`DELETE /api/messages`（clear）。

**存储 Storage**: 每房间一个 append-only `messages.jsonl`，与 `workbuddy-agent-comms` v2.1 兼容；`_id_seq.json` 保证 Clear 后 id 单调不重置（增量拉取不失聪）。Per-room append-only `messages.jsonl` (v2.1 compatible with `workbuddy-agent-comms`); `_id_seq.json` keeps ids monotonic across clear so incremental pulls never miss.

---

## 文档 | Docs

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — 设计、模块、消息生命周期 design, modules, message lifecycle
- [`docs/GUI.md`](docs/GUI.md) — Web GUI 用法 usage
- [`docs/INTEGRATION.md`](docs/INTEGRATION.md) — 用 curl / Python / Node 接 agent connect an agent via curl / Python / Node
- [`docs/AGENT_CONNECT.md`](docs/AGENT_CONNECT.md) — 教 agent 连入并接上 @（责任边界 + ocid）teach an agent to join and hook up `@`

## License

[MIT](LICENSE)