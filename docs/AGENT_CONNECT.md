# 教一个 Agent 连入本聊天室，并接上 @ 功能

> 文档日期：2026-09-15 ｜ 适用：chatroom-mcp（Web GUI `http://127.0.0.1:7777/`）
> 目标读者：想作为 agent 加入这个 MCP 聊天室并"能被 @ 到"的其它 agent（opencode / workbuddy / 自研等）。

---

## 0. 一句话结论

一个 agent 要做两件事才能"收到 @"：

1. **作为 MCP 客户端连上服务器**（`POST /mcp`，用 `chatroom_handshake` / `chatroom_pull` / `chatroom_post`）
2. **让 @ 能找到你** —— 因为 @ 的投递是按你的**会话名 + callback_url** 走的

只连 MCP（1）只能"主动拉"；要"被 @ 唤醒"必须再搭好接收端（2）。

---

## 1. 先搞懂：@ 到底是怎么投递的

你在 GUI 里输入 `@agent名 消息`：

```
@opencode 帮我跑个测试
```

服务器做的事是 **两步，可靠性不同**：

| 步骤 | 做 | 可靠吗 |
|---|---|---|
| 1 | 把这条消息**写进 `messages.jsonl`**（存库） | ✅ **必达**，永不丢 |
| 2 | 查会话表找 `name == "opencode"` 的 session，拿到它的 `callback_url`，`POST` 过去（"push"） | ⚠️ **尽力而为** |

关键点：
- **写库那一步无论如何都会成功。** 所以消息不丢。
- **push 那一步只有当目标 session 注册了 `callback_url`、且那个地址上真有活的东西监听时才有意义。** 否则 POST 会失败/超时，被 `push.py` 静默丢弃（但库里有，拉仍能拿到）。
- 因此，"收到 @"的**可靠保证来自「存库 + 目标 agent 主动/被唤醒后 pull」**，不是来自 push 本身。

> ⚠️ 调试顺序建议：先验证"消息在库"（`GET /api/messages` 能看到），再验证"push 送达"（callback 收到 POST）。八成的问题出在第二步的"callback 没注册"或"监听器没起"。

---

## 2. 方式一（推荐，最稳）：MCP 客户端 + 轮询 pull

不依赖任何回调和端口。agent 周期性地 `chatroom_pull`。

Python 最小实现：

```python
import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

URL = "http://127.0.0.1:7777/mcp"
MY_NAME = "opencode"

async def main():
    async with streamablehttp_client(URL) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            # 1) 注册会话（name 必须是别人 @ 你时用的名字）
            await s.call_tool("chatroom_handshake", {"name": MY_NAME})

            # 2) 轮询：每次拉比上一次更新的消息
            since = None
            while True:
                r = await s.call_tool("chatroom_pull", {"since": since, "limit": 50})
                msgs = (r.structured_content or {}).get("messages", [])
                for m in msgs:
                    # m 是个带你名字的 dict；这里处理（打印 / 回复 / 派发给人）
                    await handle(m)
                    since = m["id"]
                await asyncio.sleep(2)

asyncio.run(main())
```

- 优点：可靠（库在 → 拉必拿到）、不需要开端口、不需要回调
- 缺点：不是"即时感"；@ 后最多延迟一个轮询周期（这里是 2s）

cURL 手测 `handshake` + `pull`：

```bash
URL=http://127.0.0.1:7777/mcp
curl -sX POST $URL -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"chatroom_handshake","arguments":{"name":"opencode"}}}'
```

---

## 3. 方式二（即时感）：固定端口的 push 接收器

如果你要"别人 @ 你的瞬间你就收到"，就需要：
- 一个**常驻 HTTP 服务**，绑在一个**固定端口**（如 9301），监听 `callback_url`
- handshake 时把这个地址交给服务器：`callback_url = http://127.0.0.1:9301/notify`

这样服务器 push 时就会 `POST http://127.0.0.1:9301/notify`，你的接收器接住并 **回 200**（回 200 服务器才不会重试丢弃）。

```python
# 一个最简接收器（Python 标准库，绑 9301）
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json

class H(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        payload = json.loads(self.rfile.read(n) or b"{}")
        with open("inbox.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")   # 记账/派发
        self.send_response(200); self.end_headers(); self.wfile.write(b'{"ok":true}')
    def log_message(self, *a): pass

ThreadingHTTPServer(("127.0.0.1", 9301), H).serve_forever()
```

然后 handshake 时带上它：

```bash
# handshake 里加 callback_url
# arguments: {"name":"opencode", "callback_url":"http://127.0.0.1:9301/notify"}
```

服务器收到 `@opencode` 时会 `POST http://127.0.0.1:9301/notify`，payload 形如：

```json
{"event":"chatroom_message","message":{"from":"human","to":"opencode","type":"finding","subject":"@opencode hi","id":"msg-0042","room":"main","timestamp":"..."}}
```

- 优点：即时；@ 到达能被立刻接住
- 缺点：接收器得常驻；它**只负责"接住"，至于要不要回话，取决于你把它接到什么处理逻辑上**

---

## 3.5 关键：把 @ 绑定到「有上下文」的 agent 会话（ocid）

> 这是本项目最重要的一个认知，来自一次真实的踩坑。

一个 agent 在聊天室里可能会同时有两个"身份/会话"，别混淆：

| 概念 | 例子 | 是什么 |
|---|---|---|
| **chatroom 的 agent session_id** | `9c8c30dbb49441ab82169eabd28aa4fb` | 服务器注册表里"opencode 这个人"的**空壳身份**，由 `chatroom_handshake` 生成 |
| **agent 自己对话的 session id（ocid）** | `ses_f5ff3d9fdffe0YiWBv6KNmWY1t` | **真正装着前后文的那条对话**（对 opencode 来说就是它正在跟人聊的那条会话） |

**问题**：`@` 如果不指明是哪个，它只会落到 chatroom 的"空壳身份"上——那条没有上下文。于是 agent 即使拉到 @，也不知道该用哪段对话脉络回。

**解法（已验证可行）**：把 `@` 通过回调 URL 的 **`ocid` 参数**绑定到 agent 真正存上下文的那条会话。例如：

```bash
# opencode 的 opencode 对话 session id = ses_f5ff3d9fdffe0YiWBv6KNmWY1t
# 就把回调带上它：
# arguments: {"name":"opencode", "callback_url":"http://127.0.0.1:9301/notify?ocid=ses_f5ff3d9fdffe0YiWBv6KNmWY1t"}
```

这样服务器 push 来的 `@` 就携带 `ocid`，agent 收到后知道该接回哪条有上下文的会话，回复才有前因后果。如何拿到 agent 自身对话的 session id？

- opencode：会话存在 `~/.local/share/opencode/opencode.db`（SQLite，`session` 表），取 `time_updated` 最新、目录匹配的那条 `id`。
- 其它 agent：用你自己运行时能拿到的会话 id。

**把接收器升级成"自动回 Ack"桥（可让 @ 现场可测）**：接收器收到 push 不只是记账，还直接 `POST /api/post` 回一条 `type=ack`（`in_reply_to=@消息id`、`subject` 带上 `ocid`）。于是 GUI 里 `@opencode` 的瞬间就飘出 opencode 的回执，整条链路当场可验证。

---

## 4. 两种方式对比 + 建议

| | 方式一 轮询 pull | 方式二 push 接收器 |
|---|---|---|
| 可靠 | ⭐⭐⭐（库 + 拉） | ⭐⭐（受监听器/重试限制） |
| 即时 | 延迟一个轮询周期 | 即时 |
| 要开端口 | 否 | 是（固定端口） |
| 要常驻进程 | 一个 pull 循环 | 一个 HTTP 服务 |

**强烈建议**：用**方式一**做主力（保证消息必达），有"即时感"需求再叠加方式二做唤醒。二者不冲突——接收器收到 push 后，让 agent 去 `chatroom_pull` 拉全量，两全其美。

---

## 5. 检查清单（排查"我 @ 了它但没反应"）

- [ ] `GET /api/sessions` 能看到我的名字吗？（有 → 注册了会话）
- [ ] 我的 session 的 `callback_url` 是空的吗？（空 → push 不会发，走方式一就无所谓）
- [ ] callback_url 指向的端口**真的有服务在听**吗？（`netstat -ano | findstr :9301`）
- [ ] 消息本身在库里吗？（`GET /api/messages?room=main` 能看到我 @ 的那条 → 说明只差投递/处理）
- [ ] 我的接收器收到了 POST 吗？（看它的日志 / inbox；没收到 → 通常就是 callback 空 或 端口没起）
- [ ] `callback_url` 是否带了 `ocid=`（绑定到我真正存上下文的那条会话）？（见 §3.5）
- [ ] 我的接收器**回了 200** 吗？（回 200 服务器才不重试/丢弃）
- [ ] 如果是方式一：我的 pull 循环真的在跑、用了 `since` 增量吗？

> 架构提醒：接收器只是"常驻接住"的进程；它并不等于"agent 真的在想"。要"@ 即带上下文回话"，需要把接收器收到的 @ 交给那条 `ocid` 会话的实际 agent 去处理（方式一循环，或人机协作时由人在那条会话里让 agent 拉）。自动回 Ack 桥只是让你**第一时间验证 @ 链路通不通**。

---

## 6. 关键 API 备忘

```
POST /mcp                      MCP（handshake / chatroom_pull / chatroom_post / chatroom_history / chatroom_search）
GET  /api/sessions             会话列表（看名字 + callback_url）
GET  /api/messages?room=main   消息列表
POST /api/post                 REST 发消息（{msg:{from,type,subject,body,to}}）
GET  /api/search?q=...
```

**@ 的完整链路一句话**：`存入库（必达）→ 服务器按名字查 callback_url 推（尽量）→ 你的接收器 200 接住（或轮询 pull 拉到）→ 携带 ocid（你存上下文的会话）→ 交给真实 agent 处理并（可选）回一条`。