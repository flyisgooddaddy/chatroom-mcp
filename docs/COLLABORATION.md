# COLLABORATION — chatroom-mcp 双 Agent 协作约定

> 文档日期：2026-09-24
> 适用：chatroom-mcp 项目（server + openwriter / opencode 双 agent 长期协作）
> 目标读者：openwriter / opencode / 后加入的 agent / 维护者 bobo
> 来源：吸收自 workbuddy-agent-comms v2.1 的 A1-A6 对齐纪律（msg-0134 由 opencode 提议）

---

## §0 目标与范围

本文档约束 chatroom-mcp 项目中**两个长期 agent**（openwriter + opencode）的协作行为：
代码 owner、消息约定、对齐纪律、撞车治理。**不**约束临时跑测试的 agent。

跟 `STATUS_chatroom_2026_09_24.md`（bobo 桌面入口）互补：
- **STATUS.md** = milestone + commit map + proposals（一次性总结）
- **COLLABORATION.md**（本文）= 长期协议文档（chatroom-mcp repo 内）

---

## §1 OWNER 表

代码区域的 owner。冲突时按此表找 reviewer。

| 区域 | Owner | 备注 |
|---|---|---|
| `src/chatroom/server.py` (核心 REST + MCP) | openwriter | server 协议权威 |
| `src/chatroom/protocol.py` (消息 schema) | openwriter | msg type (finding/plan/request/ack/requestion/done/blocked) 定义 |
| `src/chatroom/store.py` (JSONL 持久化) | openwriter | 消息存储 + 检索 |
| `src/chatroom/sessions.py` (agent 注册) | openwriter | handshake + bound_session_id + machine |
| `src/chatroom/hub.py` (WebSocket/SSE 广播) | openwriter | |
| `src/chatroom/push.py` (callback 投递) | openwriter | |
| `src/chatroom/tui/` (Textual GUI) | openwriter | |
| `src/chatroom/web/` (HTTP GUI) | openwriter | |
| `src/chatroom_client/` (opencode 端客户端) | opencode | opencode bridge |
| `docs/COLLABORATION.md`（本文） | openwriter draft + opencode review | 共同维护 |
| `docs/ARCHITECTURE.md` / `AGENT_CONNECT.md` / `LAN_DEPLOY.md` | openwriter | |
| `docs/WORKBUDDY_ADAPTER.md` | opencode | opencode bridge 适配 |
| `docs/ROADMAP.md` | shared | 共同维护 |
| `tests/` | 改谁代码谁写测试 | — |
| `_runtime/` | openwriter | server runtime |
| `examples/opencode-plugin/` + 部署版 `~/.config/opencode/plugins/chatroom-bridge.ts` | opencode | 部署版真源两处同步 (改 examples 必须同步部署版, 无版本分叉) |
| `GET /api/schema-version` 消费 (bridge 启动校验) | opencode | 层 C, 下迭代 |

---

## §1.5 bridge(opencode)侧 — owner 边界 & 运营

源自 opencode msg-0287 桥段提案。

- **Owner**: `examples/opencode-plugin/chatroom-bridge.ts` + 部署版 `~/.config/opencode/plugins/chatroom-bridge.ts`
  (**单一真源**, 每次改 examples 必须同步到部署版; 两处一致, 无版本分叉)
- **框架**: opencode plugin, **PULL 模式** (long-poll `/api/messages/poll` 注入).
  依赖 `@opencode-ai/plugin` SDK, 不独立跑。
- **部署/生效**: 改 bridge 后需**重启 opencode** (plugin 启动期加载)。
  `CHATROOM_HTTP` env 可覆盖 server 地址。
- **bridge 注入增强**:
  - world 快照 (`me` / `online` / `co_agents` / `machine`)
  - 多目标 `to` 展示
  - handshake 上报 `MY_MACHINE`
- **schema endpoint**: 本机的 [层 C] `opencode/chatroom GET /api/schema-version`，
  bridge 启动时读取并校验 (**不匹配拒启 / 强更新**) — 预留, 下迭代。

---

---

## §2 撞车治理 — 4 类时间耦合撞车 + 3 层改进

源自 2026-09-24 撞车分析（openwriter msg-0131 + opencode msg-0132）。

### 4 类时间耦合撞车

- **类 1: 同一文件同时改** — 双方各改一处不重叠但触发冲突。治：先广播"我打算改 X 文件" → 等 ack → 改。
- **类 2: 字段语义漂移** — 一方改了字段含义，另一方按旧语义读。治：done 必带 criteria_from 血缘。
- **类 3: 部署节奏不同步** — server 跟 opencode bridge 各 restart 时刻不同，中间窗口行为不一致。治：广播 schema-version + 双方确认。
- **类 4: 同源字段升级不可见** — server 端加字段，opencode 不知道。治：广播协议变更 + 等 ack。

### 3 层改进（按优先级）

- **层 A（这轮做）**: 文档 + CODEOWNERS（本文 + 后续 `.github/CODEOWNERS`）
- **层 B（这轮做）**: 协议变更广播 + 等 ack（msg type=plan/request 必 ack，复述 brief）
- **层 C（next 迭代）**: schema-version 机制 — server 暴露 `GET /api/schema-version`，bridge 启动校验

### 类 2 + 类 4 同根

两者本质是 schema 无 version 导致"字段升级不可见"。schema-version 一剂药通治。

### 类 3 显式子项

两端部署时刻不同（server restart vs opencode 重启各自独立）必有短暂漂移。即使有 schema-version，
靠 broadcast + version 标识 + 双方确认降低。

---

## §3 A1-A6 对齐纪律

吸收自 **workbuddy-agent-comms v2.1**（chatroom 之前用 `.workbuddy/comms/` 文件系统收发）。
msg type（finding/plan/request/ack/requestion/done/blocked）已原样继承到 chatroom。
对齐纪律 A1-A6 比单纯文件 owner 更能防跑偏/撞车。

### A1: brief 是开工前契约

`plan` / `request` 消息必须含 **brief 段**（what / why / criteria）。无 brief → 对方不执行。

### A2: 收到 plan/request 必回 ack

ack 必须**逐条复述对方 brief**，确认一致 → agreed；任一不一致 → requestion，不得先开工。
防"各干各的"时间耦合撞车。

### A3: 口径必须一致才能比结论

what / why / criteria 必须双方一致才开工。**how** 允许不同实现，但**口径**（数据/映射/hold）必须一致，
否则结论不可比。
示例：今天对 `opencode.machine` 字段空/非空的判断差点各说各话 — A3/A6 治。

### A4: 🔴 禁止在 done 里首次引入判据

done 只能用开工前已 ack 的 criteria。判据可追溯（`criteria_from` 血缘），不与父文字逐字一致也接受。
防"自创判据"跑偏，对应类 2 字段语义漂移。

### A5: 组合结论必须多档

组合/复合结论，criteria 必须含**多档**（≥2 档）。量化铁律在原 workbuddy 是 `hold≥30`，移植时**精神保留**
（判据可追溯 + 多档），数值化不强加 — 两 agent 都是 qualitative。

### A6: 维度关闭必须写覆盖口径

结论依赖"维度关闭 / 方向无效" → brief 的 how 必须写清覆盖口径。没测 = `not_doing`，不得上升为"无效"。
防把"没做"说成"无效"。

### A1-A6 映射到 chatroom 实操

| 原则 | chatroom 实操 |
|---|---|
| A1 brief | `type=plan / request` 的 body 含 "brief 段"（what/why/criteria 标题） |
| A2 ack 复述 | `type=ack` 的 body 含 "brief — 我复述" 段, 逐条对应对方 brief |
| A3 口径 | `type=finding / done` 的 body 引用对方 ack 的口径, 不引入新口径 |
| A4 判据 | `type=done` 的 criteria 必须能在 ack 的 brief 里找到 `criteria_from` |
| A5 多档 | `type=finding` 的结论段引 ≥2 档证据（对应 ack brief 里的 criteria 列表） |
| A6 覆盖 | `type=finding / done` 的 how 段明确 "覆盖 X, 没测 Y (= not_doing)" |

---

## §4 协议变更广播 + 等 ack（层 B 实操）

任何对以下内容的改动必须先发 `type=plan` 等 ack：

- server 协议 schema（msg 字段、endpoint、status code）
- bridge 行为（handshake 字段、pull/post 频率、callback_url 语义）
- 共享数据格式（messages.jsonl 字段、_sessions.json 字段）
- 文档结构（OWNER 表、A1-A6 段、撞车治理）

**plan 必须含**：

- **brief 段**: what / why / criteria / how
- **影响范围**: 涉及的文件 / 模块 / 协议字段
- **回滚方案**: 如果 ack 后对方不同意或实施失败怎么回

---

## §5 双 Agent 工作流（典型 cycle）

1. **openwriter** 发 `type=plan`（含 brief）到 opencode
2. **opencode** 回 `type=ack`（逐条复述 brief）或 `type=requestion`（不一致点）
3. 若 ack → openwriter 开分支 / 改代码
4. **openwriter** 发 `type=done`（引用 ack 的 criteria_from + how 覆盖 + not_doing 段）
5. **opencode** review done，回 `type=ack` 同意合 main 或 `type=requestion` 退回去
6. 双方任何一方 review 不通过 → 回 step 1（带新 brief）

撞车治理落到工作流：
- 类 1: step 1 的 brief 里写明"涉及文件 X"，对方 ack 时确认不冲突
- 类 2: step 4 的 done 必带 criteria_from 血缘
- 类 3: step 4 的 done 必含"部署影响 + how 覆盖"段
- 类 4: step 1 的 brief 必写"新字段 + 老字段兼容矩阵"

---

## §6 安全 / Credentials

- 任何 chatroom 里明文泄露的 token / 密钥必须**立即**在 @human 消息里 @bobo 撤销
- 撤销记录在 §6.1 维护（可追溯）

### §6.1 已撤销

| 时间 | token / 凭证 | 撤销者 | 来源 |
|---|---|---|---|
| 2026-09-24 | GitHub PAT (已脱敏/完整值见 bobo GitHub settings) | bobo | chatroom 历史 |
| 2026-09-24 | GitHub PAT (已脱敏) | bobo | chatroom 历史 |

---

## §7 部署 / 拓扑

- **chatroom-mcp server**: 部署在 `192.168.31.201:7777`（**opencode (DESKTOP-6146NJR) Windows 机**，
  跑 `chatroom.exe` 进程 listen `0.0.0.0:7777`，**不是 Linux root/systemd**）
- **openwriter (bobo-T300LA, Linux)**: 通过 `127.0.0.1:7777`（本地 TCP 转发 `/tmp/tcp_forward.py` → `192.168.31.201:7777`）
  或未来通过 `adapter.py` 的 `CHATROOM_URL` env 直接连
- **opencode (DESKTOP-6146NJR, Windows)**: 直连 `localhost:7777`（server 同机）

未来: server 多副本 + DNS / 端口漂移。

---

## §8 待办（next 迭代）

- [ ] **schema-version** — server 暴露 `GET /api/schema-version`, bridge 启动校验（类 2 + 类 4 治本）
- [ ] **CODEOWNERS** — 写 `.github/CODEOWNERS`，把 §1 OWNER 表翻译成 CODEOWNERS 规则（GitHub PR 自动 reviewer）
- [ ] **adapter env** — `workspace/_tools/chatroom/adapter.py` 加 `CHATROOM_URL` env 覆盖
- [ ] **bobo 反复注销真因** — 排查 `xfce4-power-manager` / 锁屏超时 / 显卡驱动 crash（轻 P0）

---

## §9 修订记录

| 日期 | 改动 | 作者 | 来源 |
|---|---|---|---|
| 2026-09-24 | 初版（A1-A6 + 4类/3层 + OWNER 表） | openwriter | msg-0136 (opencode 拍板) + msg-0138 (bobo 拍板) + msg-0131/0132 (撞车分析) |