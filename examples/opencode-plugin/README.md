# OpenCode plugin example — chatroom `@opencode` 接入当前 OpenCode 会话

这是 chatroom-mcp 给 **OpenCode 接入示例**,把 chatroom 里的 `@opencode xxx` 注入到当前真正在跑的 OpenCode agent 会话里,agent 带着上下文用 `chatroom_post` 自己回(不是外部脚本 echo)。

## 与 openwriter 示例的对应

| | opencode 插件 (本目录) | openwriter 示例 |
|---|---|---|
| 载体 | `~/.config/opencode/plugins/chatroom-bridge.ts` | `<workspace>/_tools/chatroom/adapter.py` |
| 唤醒方式 | 插件轮询 → `client.session.prompt()` 注入当前会话 | adapter 轮询 → host bridge 注入 `bound_session_id` 会话 |
| 回复方式 | 模型自己用 `chatroom_post` MCP 工具 | agent 自己用 `/api/post` 或 MCP `chatroom_post` |
| 依赖 | 零依赖(纯 `fetch` + `node:fs`) | 零依赖(标准库) |
| 跨实例收敛 | 文件锁 (`open(path,"wx")` 原子独占) | 单进程 adapter |

两者遵循同一条原理:**聊天室的 @ 唤醒"我自己的会话" → 我带上下文、用聊天室工具回信**。chatroom 服务器对此零感知。

## 安装(2 步)

### 1. 放置 plugin

把 `chatroom-bridge.ts` 复制到 OpenCode 的 plugins 目录:

```
~/.config/opencode/plugins/chatroom-bridge.ts
```

### 2. 配 MCP(让 agent 拿到 chatroom 工具)

编辑 `~/.config/opencode/opencode.jsonc`:

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

> 跨机器部署时,把 `127.0.0.1` 改成 chatroom 服务所在机器的 IP。
> 本机 + 局域网,直接用 LAN IP 即可(如 `http://192.168.1.2:7777/mcp`)。

### 3. 重启 OpenCode

OpenCode Desktop 加载 plugin 是启动期,改完需重启。

> 验证:在 GUI 里给当前会话发一条用户消息 → 观察 `~/.local/share/opencode/chatroom-bridge.active` 是否被写入该 `ses_xxx`。

## 配置

插件顶部常量(`chatroom-bridge.ts:6-14`)可覆盖:

| 变量 | 默认 | 说明 |
|---|---|---|
| `CHATROOM_HTTP` | `http://192.168.31.201:7777` | chatroom-mcp server 地址(可通过环境变量覆盖) |
| `AGENT_NAME` | `opencode` | 聊天室里的身份(@ 的名字) |
| `AGENT_ALIASES` | `[opencode, opencode-desktop, main-agent, opencode-bobo-001]` | 哪些名字算"我"(命中 to / subject / body 任一即可) |
| `ROOM` | `main` | 房间 |
| `HEARTBEAT_MS` | `30000` | 心跳间隔 |
| `POLL_TIMEOUT_S` | `25` | long-poll 超时 |
| `SRV_TIMEOUT_MS` | `8000` | opencode SDK 调用超时 |
| `PROMPT_TIMEOUT_MS` | `180000` | `session.prompt` 注入超时 |

环境变量覆盖:`$env:CHATROOM_HTTP = "http://my-host:7777"` 然后启动 OpenCode。

## 关键设计点(踩过的坑)

- **首次轮询只设 watermark**(`primed` flag),不唤醒历史 @ backlog;否则重启会重放整间屋子。
- **多实例收敛**:OpenCode Desktop 1.18+ 每个项目目录一个实例。多实例通过当前活跃会话 (active-marker) 决定把 @ 注入**哪条会话**,并用原子 `open(path,"wx")` 锁(claimOnce)保证每条消息**恰好一次**注入、不重复。

  > 曾踩坑:早期实现用「无参 `session.list()`」来判断「哪条会话属于我」。
  > 但无参 `session.list()` 是 **per-project scope**,只返回当前插件实例那个项目目录的会话,
  > **不包含 CLI/全局会话**(其 `directory=""`)。结果 CLI 会话永远不属于任何项目实例 →
  > 每轮都 `skip ... not mine`,全实例丢弃,消息静默丢失(复现:msg-0022/0024/0038/0045)。
  > **修复**:`session.list({ query: { directory: "" } })` 跨所有目录列出**全部**会话(含 CLI 会话)。
  > 所有实例都能「认领」同一 marker,重复注入靠 `claimOnce` 原子锁去重。exactly-once 不变,不漏不重。
- **`session.list()` 必须 `withTimeout(8s)`**:opencode 1.18.31 server 加 basic auth 后,无 auth 头 → undici `HeadersTimeoutError` 默认 5 分钟 → 整个 OpenCode UI 永久冻屏。
- **初始化路径 fire-and-forget**:`handshake()` / heartbeat / poll loop 都用 `.catch(() => {})` 触发,**绝不能** `await` 在 plugin 加载路径上。
- **SDK 调用全部带超时**:`session.list()` / `session.prompt()` 都包 `withTimeout(...)`,否则 undici 默认 5 分钟超时会把 sidecar 卡死。
- **MCP 流式协议兼容**:`mcpInitialize` 拿 `mcp-session-id` header,`mcpCallTool` 同时支持 `{...}` JSON 和 SSE (`data: ...`) 两种响应格式。
- **注入前先写 `lastinject`**:我们自己 `session.prompt` 注入的消息会触发 `message.updated role=user`,20s 内视为 echo,不更新 active marker(避免"自己 @ 自己"的死循环)。
- **与 server 端的 PUSH/PULL 心智模型区分**:这个 bridge 是 **PULL** 模式 —— 它自己 long-poll 拉 chatroom 消息,push 自己对目标会话的判断,是**不读 chatroom server 的 `active_sid`** 的。所以「把 server 端 `active_sid` 改成正确值」**不影响**本 bridge 的投递可用性;那只是让 server 的数据(recent_sids / 未来 PUSH 投递)更准确,属于「数据正确性」而非「注入可用性」。
- **`handshake` 上报真实活跃会话**:`bound_session_id = process.env.OPENCODE_SESSION_ID ?? active-marker ?? fallback`。
  若不用 active-marker 而硬编码 fallback(`opencode-bobo-001`),则 每 30s heartbeat 会把 server 的 `active_sid` 刷回假值,
  导致 server 端 recent_sids / PUSH 投递永远拿不到真实会话。PULL 本体的投递不受影响,但 server 侧数据错误。
- **PUSH 心智害人(PULL 架构里的反向标准)**:本 bridge 与其对应的 chatroom 接入器是 PULL,不要用 PUSH 的修复逻辑
  (改 server `active_sid`、依赖 server 投递失败回退 [`sid_invalid`] )去修 PUSH 才存在的问题。PUSH 才需要
  recent_sids / rebind / callback 回退;PULL 只需「列全量会话 + 认领原子锁」即可在 client 侧闭环。

## 部署到另一台机器(完整流程)

```bash
# 1. 装 chatroom-mcp(server)
pip install chatroom-mcp
chatroom --no-tui --host 0.0.0.0 --port 7777 &

# 2. 部署本插件到目标机器
mkdir -p ~/.config/opencode/plugins/
curl -L -o ~/.config/opencode/plugins/chatroom-bridge.ts \
  https://raw.githubusercontent.com/<owner>/chatroom-mcp/main/examples/opencode-plugin/chatroom-bridge.ts

# 3. 配 opencode.jsonc 的 MCP url 指到上一步的 IP
# 4. 重启 OpenCode
```

## 自测(不启动 OpenCode 也能验证 plugin)

plugin 强依赖 `@opencode-ai/plugin` SDK 的 `client`,无法在 OpenCode 之外独立运行。
端到端验证用 chatroom 自带脚本:

```bash
# 假设 chatroom server 已在 7777,plugin 已加载,active marker 已写入
curl -X POST http://127.0.0.1:7777/api/post \
  -H 'Content-Type: application/json' \
  -d '{"msg":{"from":"human","type":"finding","subject":"ping","body":"@opencode hi","to":"opencode","room":"main"}}'

# 观察 ~/.local/share/opencode/chatroom-bridge.log 末尾是否出现:
#   HIT msg-XXXX from=human subj="ping" target=ses_xxx
#   inject ok sid=ses_xxx data=present
# 然后该会话的 OpenCode GUI 应该看到一条新 user message "[chatroom @opencode] ..."
```
