# OpenCode plugin example — chatroom `@opencode` 接入当前 OpenCode 会话 | `@opencode` into the current OpenCode session

这是 chatroom-mcp 给 **OpenCode 接入示例**,把 chatroom 里的 `@opencode xxx` 注入到当前真正在跑的 OpenCode agent 会话里,agent 带着上下文用 `chatroom_post` 自己回(不是外部脚本 echo)。

This is chatroom-mcp's **OpenCode integration example**. It injects `@opencode xxx` from chatroom into the currently-running OpenCode agent session, where the agent replies with full context via its own `chatroom_post` — no external echo script.

## 与 openwriter 示例的对应 | Correspondence with the openwriter example

| | opencode 插件 (本目录) this dir | openwriter 示例 openwriter example |
|---|---|---|
| 载体 Host | `~/.config/opencode/plugins/chatroom-bridge.ts` | `<workspace>/_tools/chatroom/adapter.py` |
| 唤醒方式 Wake | 插件轮询 → `client.session.prompt()` 注入当前会话 plugin polls → injects current session | adapter 轮询 → host bridge 注入 `bound_session_id` 会话 |
| 回复方式 Reply | 模型自己用 `chatroom_post` MCP 工具 model uses `chatroom_post` tool | agent 用 `/api/post` 或 MCP `chatroom_post` |
| 依赖 Deps | 零依赖(纯 `fetch` + `node:fs`) zero-dep | 零依赖(标准库 stdlib) |
| 跨实例收敛 Convergence | 文件锁 (`open(path,"wx")` 原子独占 atomic lock) | 单进程 adapter single-process |

两者遵循同一条原理:**聊天室的 @ 唤醒"我自己的会话" → 我带上下文、用聊天室工具回信**。chatroom 服务器对此零感知。Both follow the same principle: an `@` from the room wakes "my own session" → I reply with context using the chatroom tool. The chatroom server is unaware of this.

## 安装(2 步)| Install (2 steps)

### 1. 放置 plugin | Place the plugin

把 `chatroom-bridge.ts` 复制到 OpenCode 的 plugins 目录:Copy `chatroom-bridge.ts` to OpenCode's plugins dir:

```
~/.config/opencode/plugins/chatroom-bridge.ts
```

### 2. 配 MCP(让 agent 拿到 chatroom 工具)| Configure MCP (give the agent the chatroom tools)

编辑 `~/.config/opencode/opencode.jsonc`: Edit `~/.config/opencode/opencode.jsonc`:

```jsonc
{
  "mcp": {
    "chatroom": {
      "type": "remote",
      "url": "http://127.0.0.1:7777/mcp"
    }
  }
}
```

> 跨机器部署时,把 `127.0.0.1` 改成 chatroom 服务所在机器的 IP。For cross-machine deploys, replace `127.0.0.1` with the machine hosting the chatroom server.

### 3. 重启 OpenCode | Restart OpenCode

OpenCode Desktop 加载 plugin 是启动期,改完需重启。Plugins load at startup; restart after changing.

> 验证 Verify:在 GUI 里给当前会话发一条用户消息 → 观察 `~/.local/share/opencode/chatroom-bridge.active` 是否被写入该 `ses_xxx`。Send a user message in the GUI and check whether `chatroom-bridge.active` records that `ses_xxx`.

## 配置 | Config

插件顶部常量(`chatroom-bridge.ts` 顶部 constants)可覆盖:

| 变量 Variable | 默认 Default | 说明 Notes |
|---|---|---|
| `CHATROOM_HTTP` | `http://127.0.0.1:7777` | chatroom-mcp server 地址(可通过环境变量覆盖 env-overridable) |
| `AGENT_NAME` | `opencode` | 聊天室里的身份(@ 的名字) identity in the room (`@` name) |
| `AGENT_ALIASES` | `[opencode, opencode-desktop, main-agent, opencode-bobo-001]` | 哪些名字算"我"(命中 to / subject / body 任一即可) which names count as "me" (to / subject / body match) |
| `ROOM` | `main` | 房间 room |
| `HEARTBEAT_MS` | `30000` | 心跳间隔 heartbeat interval |
| `POLL_TIMEOUT_S` | `25` | long-poll 超时 long-poll timeout |
| `SRV_TIMEOUT_MS` | `8000` | opencode SDK 调用超时 SDK-call timeout |
| `PROMPT_TIMEOUT_MS` | `180000` | `session.prompt` 注入超时 inject timeout |

环境变量覆盖 Env override:`$env:CHATROOM_HTTP = "http://my-host:7777"` 然后启动 OpenCode then start OpenCode。

## 关键设计点(踩过的坑)| Design notes (pains we hit)

- **首次轮询只设 watermark**(`primed` flag),不唤醒历史 @ backlog;否则重启会重放整间屋子。Only set a watermark on the first poll (`primed`); don't replay historical `@` backlog or restarts replay the whole room.
- **多实例收敛 Convergence**:OpenCode Desktop 1.18+ 每个项目目录一个实例。通过当前活跃会话 (active-marker) 决定把 @ 注入哪条会话,并用原子 `open(path,"wx")` 锁(`claimOnce`)保证每条消息**恰好一次**注入、不重复。Each project dir is one instance. The active session marker decides which session gets the `@`, and an atomic lock (`claimOnce`) guarantees exactly-once — no duplicates.

  > 曾踩坑 Pitfall:早期用「无参 `session.list()`」判断「哪条会话属于我」。但无参 `session.list()` 是 **per-project scope**,只返回当前项目目录的会话,**不包含 CLI/全局会话**(其 `directory=""`)。结果 CLI 会话永远不属于任何项目实例 → 每轮都 `skip ... not mine`,全实例丢弃,消息静默丢失(复现:msg-0022/0024/0038/0045)。Early code used bare `session.list()`, which is **per-project scope** and **excludes CLI/global sessions** (`directory=""`). That made a CLI session never belong to any project instance → every round skipped and dropped (repro: msg-0022/0024/0038/0045).
  > **修复 Fix**:`session.list({ query: { directory: "" } })` 跨所有目录列出**全部**会话(含 CLI 会话)。所有实例都能「认领」同一 marker,重复注入靠 `claimOnce` 原子锁去重。exactly-once 不变,不漏不重。List ALL sessions across directories (incl. CLI); any instance claims, duplicates deduped by `claimOnce`. Exactly-once preserved, no drops, no dups.
- **`session.list()` 必须 `withTimeout(8s)`**:opencode 1.18.31 server 加 basic auth 后,无 auth 头 → undici `HeadersTimeoutError` 默认 5 分钟 → 整个 OpenCode UI 永久冻屏。Must wrap in `withTimeout(8s)`; after basic auth (1.18.31), a missing auth header causes a default 5-minute undici timeout that freezes the whole UI.
- **初始化路径 fire-and-forget**:`handshake()` / heartbeat / poll loop 都用 `.catch(() => {})` 触发,**绝不能** `await` 在 plugin 加载路径上。Init paths must be fire-and-forget; never `await` on the plugin load path.
- **SDK 调用全部带超时**:`session.list()` / `session.prompt()` 都包 `withTimeout(...)`。All SDK calls need a timeout.
- **MCP 流式协议兼容**:`mcpInitialize` 拿 `mcp-session-id` header,`mcpCallTool` 同时支持 `{...}` JSON 和 SSE (`data: ...`) 两种响应格式。Handles both JSON and SSE response formats.
- **注入前先写 `lastinject`**:我们自己 `session.prompt` 注入的消息会触发 `message.updated role=user`,20s 内视为 echo,不更新 active marker。Write `lastinject` before injecting so our own echo is ignored for 20s.
- **与 server 端的 PUSH/PULL 心智模型区分 | PULL vs PUSH mindset**:这个 bridge 是 **PULL** 模式——它自己 long-poll 拉 chatroom 消息,**不读 chatroom server 的 `active_sid`**。所以「把 server 端 `active_sid` 改成正确值」**不影响**本 bridge 的投递可用性;那只是让 server 数据(recent_sids / 未来 PUSH)更准确,属「数据正确性」而非「注入可用性」。This bridge is **PULL** — it long-polls and does NOT read the server's `active_sid`. Fixing the server's `active_sid` is "data correctness", not "delivery availability".
- **`handshake` 上报真实活跃会话**:`bound_session_id = process.env.OPENCODE_SESSION_ID ?? active-marker ?? fallback`。若硬编码 fallback,每 30s heartbeat 会把 server 的 `active_sid` 刷回假值,recent_sids / PUSH 永远拿不到真实会话。Report the real active session in `handshake`; a hardcoded fallback keeps poisoning `active_sid` every heartbeat.
- **PUSH 心智害人(PULL 架构里的反向标准 | the anti-pattern)**:本 bridge 是 PULL,不要用 PUSH 的修复逻辑(改 `active_sid`、依赖 `sid_invalid` 回退)修 PUSH 才存在的问题。PUSH 才需要 recent_sids / rebind / callback 回退;PULL 只需「列全量会话 + 认领原子锁」即可 client 侧闭环。Don't apply PUSH fixes to this PULL adapter; PULL just needs "list all sessions + an atomic claim lock" to self-contain on the client.

## 部署到另一台机器(完整流程)| Deploy to another machine (full flow)

```bash
# 1. 装 chatroom-mcp(server) install the server
pip install chatroom-mcp
chatroom --no-tui --host 0.0.0.0 --port 7777 &

# 2. 部署本插件到目标机器 deploy the plugin
mkdir -p ~/.config/opencode/plugins/
curl -L -o ~/.config/opencode/plugins/chatroom-bridge.ts \
  https://raw.githubusercontent.com/<owner>/chatroom-mcp/main/examples/opencode-plugin/chatroom-bridge.ts

# 3. 配 opencode.jsonc 的 MCP url 指到上一步的 IP point MCP url at that IP
# 4. 重启 OpenCode restart
```

## 自测(不启动 OpenCode 也能验证 plugin)| Self-test (verify the plugin without starting OpenCode)

plugin 强依赖 `@opencode-ai/plugin` SDK,无法独立运行。端到端用 chatroom 自带脚本: The plugin needs the `@opencode-ai/plugin` SDK and can't run standalone. End-to-end test with a chatroom script:

```bash
# 假设 chatroom server 已在 7777,plugin 已加载,active marker 已写入
curl -X POST http://127.0.0.1:7777/api/post \
  -H 'Content-Type: application/json' \
  -d '{"msg":{"from":"human","type":"finding","subject":"ping","body":"@opencode hi","to":"opencode","room":"main"}}'

# 观察 ~/.local/share/opencode/chatroom-bridge.log 末尾是否出现 / expect to see:
#   HIT msg-XXXX from=human subj="ping" target=ses_xxx
#   inject ok sid=ses_xxx data=present
# 然后该会话的 OpenCode GUI 应该看到一条新 user message "[chatroom @opencode] ..."
```