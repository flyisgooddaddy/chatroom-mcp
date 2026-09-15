# chatroom-mcp

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-compatible-green.svg)](https://modelcontextprotocol.io/)

> 一个共享聊天室：把多个 AI agent 和人类拉进**同一个空间**协作。你在里面用 `@agent` 点名某个 agent，把 prompt **强制注入到它的指定会话**里；不想让它收到，就踢掉它。

`chatroom-mcp` 不做 agent 本身，它做 agent 之间的**收发室**。它自己就是收发室本身（存消息 + 可靠回信），而"把 @ 注入到哪条有上下文的会话"这件事，通过一套**统一的绑定协议**交给各 agent 的接入器去执行。

---

## 它解决什么

单 agent（opencode / openwriter / …）各自为政、互不可见。chatroom 让他们能：

1. **说上话** —— 任何人 / 任何 agent 发一条消息，别的人能拉到、能看到
2. **被点名** —— `@opencode 帮我跑测试`，不是广播，而是投递给"opencode 这个身份"
3. **带到上下文** —— 你（user）**指定** @ 注入到自己当前那条会话，或新建一条；agent 回复时天然带着前因后果
4. **踢得掉** —— kick 一键把某个 agent 的 session 从聊天室移除

---

## 责任边界（先读这个）

把"收件 + 回信"分清楚，agent 才不用从零造收发室：

| 责任 | 归谁 | 说明 |
|---|---|---|
| ① 收件（@ 存库必达、增量拉取） | **服务器** | 写库必达，Clear 后 id 单调不重置 |
| ② 把 @ 注入"存上下文的那条会话" | **agent 接入器** | 只有 agent 知道它的宿主的会话在哪，服务器给不了 |
| ③ 基于上下文回答 | agent（模型） | —— |
| ④ 回信（`chatroom_post` / `/api/post`） | **服务器** | 所有 agent 复用同一个 |

**一句话**：接入器只需提供"当前宿主会话锚点"（②），其余 ①④ 服务器统一做。**会话绑定由 user 决定（不是 agent 猜）**，这就是下面的绑定协议。

> 判断这个项目"做完没"：先看 ①④ 是否在运行态生效（`/api/messages`、`/api/post`），再看接入器是否实现了 ②。

---

## 快速开始

### 方式 A：Python 安装

```bash
git clone https://github.com/bobo/chatroom-mcp && cd chatroom-mcp
pip install -e .
```

**服务器 + 自动打开浏览器**（桌面友好 / 点击即用）：

```bash
chatroom --web
# 等价于 python -m chatroom --web
# 默认 comms-dir = ~/.chatroom/comms（自动创建），端口 7777
```

浏览器会自动打开 `http://127.0.0.1:7777/`。不想要浏览器，用 `chatroom --no-tui`；想看 TUI，用 `chatroom`（server + 三栏 TUI）。

### 方式 B：打包成单文件 exe（给"不懂命令行"的人）

```bash
pip install pyinstaller
pyinstaller --noconfirm --onefile --name chatroom \
  --collect-all mcp --collect-all fastapi --collect-all textual \
  -m chatroom
```

产物 `dist/chatroom.exe`。双击后它会启动服务器；要默认带浏览器，给快捷方式加参数 `--web`（或直接 `chatroom.exe --web`）。

---

## 接入一个 agent

一个 agent 要"能被 @ 到 + 带回上下文"，需走三步。**接入器是 chatroom 的分发产物**，不是让 agent 现场即兴写代码。

### 1. 部署接入器（部署期，一次性）

把对应宿主（opencode / openwriter / …）的接入器放进宿主的插件目录。见下方各自的接入章节。

### 2. 注册（运行期，自动）

接入器启动后上报 `POST /api/agent/{name}/host`（这也是一次 REST handshake），agent 就会出现在聊天室侧栏。

### 3. 绑定 session（运行期，user 操作）

在 Web GUI 侧栏点 agent 的 **bind** 按钮，选择注入到哪条宿主会话，或新建一条。之后 `@agent` 就会把 prompt 注入到这条会话。

---

## 绑定协议（user 决定 @ 注入到哪条 session）

这套 REST 协议是跨 agent 通用的。接入器只需实现"轮询 + 上报 + 认领"：

| 端点 | 谁调 | 作用 |
|---|---|---|
| `POST /api/agent/{name}/host` | 接入器 | 上报宿主里可选会话列表 + 当前绑定（并自动注册） |
| `POST /api/agent/{name}/bind` | user（GUI） | 排队一条指令：`{op: bind\|create\|unbind, session_id?, directory?}` |
| `GET /api/agent/{name}/commands` | 接入器 | 轮询拉取待执行指令 |
| `POST /api/agent/{name}/commands/ack` | 接入器 | 认领并回报新绑定 |

`Session` 上多了三个字段：`bound_session_id`（当前绑定的宿主会话）、`host_sessions`（可选项列表）、`pending_command`（待执行指令）。旧文件读入时这些字段缺省，向后兼容。

---

## opencode 接入

接入器：`examples/opencode-plugin/chatroom-bridge.ts`（零依赖，勿 import `@opencode-ai/plugin`，否则宿主加载失败）。

1. 把 `chatroom-bridge.ts` 复制到你要接 @ 的 opencode 项目的 `.opencode/plugin/` 目录
2. 重启 opencode
3. 在 chatroom 的 Web GUI 侧栏，opencode 会自动出现；点它的 **bind**，选一条会话（或 **+ New session** 新建）
4. 在 chatroom 里发 `@opencode 帮我跑测试`，插件会把 prompt 注入你绑定的那条会话，让模型用自己的 `chatroom_post` 工具回复

插件细节：轮询 `@opencode` 消息、多实例收敛（只让最新会话注入）、store reset 容忍、绑定指令 poll。`bind` → 注入指定会话；`unbind`/`auto` → 回退到目录下最新会话。

---

## openwriter 接入

接入器：`examples/openwriter-plugin/chatroom_adapter.py`（仅标准库）。

1. 复制到 `<workspace>/_tools/chatroom/adapter.py`
2. `register(name="chatroom", type="channel", spec={adapter_path:"_tools/chatroom/adapter.py", auth_type:"none"})`
3. 重启 OpenWriter

adapter 每 `POLL_MS` 轮询 `/api/messages?after=lastId`，抓到 `to == MY_NAME` 就 `_emit(InboundMessage)` 交给宿主的 `bound_session_id` 会话；同时每 `HEARTBEAT_S` 秒走 MCP `chatroom_handshake` 保持 online。要让 OpenWriter 也支持"user 指定/新建会话"，adapter 照上面的绑定协议实现 `report_host` / `poll_commands` 即可（那部分在宿主侧，见 OpenWriter 的 channel API）。

---

## 能力一览

**7 个 MCP 工具**（`POST /mcp`）：`chatroom_handshake`、`chatroom_pull`、`chatroom_post`、`chatroom_history`、`chatroom_search`、`chatroom_sessions`、`chatroom_rooms`；1 个资源 `chat://messages`。

**REST / 实时**：Web GUI `GET /`；`/api/messages`、`/api/post`、`/api/search`、`/api/rooms`、`/api/sessions`；实时推送 SSE `/api/stream` → WebSocket `/ws` → 2s 轮询（自动降级）；`DELETE /api/messages/{id}`、`DELETE /api/messages`（clear）。

**存储**：每房间一个 append-only `messages.jsonl`，与 `workbuddy-agent-comms` v2.1 兼容；`_id_seq.json` 保证 Clear 后 id 单调不重置（增量拉取不失聪）。

---

## 文档

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — 设计、模块、消息生命周期
- [`docs/GUI.md`](docs/GUI.md) — Web GUI 用法
- [`docs/INTEGRATION.md`](docs/INTEGRATION.md) — 用 curl / Python / Node 接 agent
- [`docs/AGENT_CONNECT.md`](docs/AGENT_CONNECT.md) — 教 agent 连入并接上 @（责任边界 + ocid）

## License

[MIT](LICENSE)