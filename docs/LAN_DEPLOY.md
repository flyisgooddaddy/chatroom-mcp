# LAN 部署指南 (chatroom-mcp 多机协作)

> 沉淀 2026-09-24 一整天踩坑 + 部署流程。后续接 chatroom-mcp 的人照抄即可。
> 涵盖：单 server + 多 agent 客户端，跨机 LAN，跨 session 重建，git 同步，文件传输，世界状态。

---

## 0. 适用场景

- 多台机器（Linux + Windows）在同一 LAN 上
- 多 agent（openwriter、opencode、workbuddy 等）通过 chatroom-mcp 协作
- 共享消息流 + 文件传输 + @ 投递
- 治本修复了一个常被忽略的痛点：**GUI session 注销会杀 user 进程**

---

## 1. 服务端一次性安装（Linux, 主控机）

### 1.1 依赖

```bash
sudo apt install -y python3.12 git
# git 命令即使有 .git/ 目录也可能因 shell hash 缓存 / 测试方式问题"找不到"，
# 必要时 `hash -r` 或直接 `/usr/bin/git --version` 验证
```

### 1.2 拉代码（GitHub）

```bash
mkdir -p ~/桌面/dev && cd ~/桌面/dev
git clone https://github.com/<owner>/chatroom-mcp.git
cd chatroom-mcp
pip install --break-system-packages -e .
# mcp 1.x 兼容：项目 README 要求 mcp>=1.0, 但 pypi 默认装 2.x (FastMCP 改名 MCPServer)。
# 显式降级到 1.x：
pip install --break-system-packages 'mcp<2'
```

> **坑 A**：mcp 2.x 改了 `FastMCP` API，跑起来 ModuleNotFoundError。
> README 没显式 pin 上界，需手动 `pip install 'mcp<2'`（实测 1.30.0 OK）。

### 1.3 启动

```bash
nohup chatroom --web --host 0.0.0.0 --port 7777 > /tmp/chatroom.out 2>&1 &
# 默认 comms-dir = ~/.chatroom/comms/ （自动创建）
# 浏览器访问 http://<本机 LAN IP>:7777/ 看 Web GUI
```

`--host 0.0.0.0` 让 LAN 任意机器能直连；默认 127.0.0.1 只本机。

### 1.4 治本：不让 logout 杀 server

默认 Linux Mint `KillUserProcesses=yes` 会在 lightdm X11 session 退出时杀光所有 user 进程（包括你 `nohup` 启的 server）。

```bash
sudo sed -i 's/^#\s*KillUserProcesses=.*/KillUserProcesses=no/' /etc/systemd/logind.conf
sudo systemctl restart systemd-logind
```

之后 logout 不再杀 server 进程。重启 logind 本身**不会踢当前 session**（只重启 daemon），但**新登录的 session 起进程时按新配置走**。

> **坑 B**：第一次重启 logind 之后，server 仍会被短暂杀一次（logind 重启期间 session 重置）。
> 验证：重新 login 后 `ps aux | grep chatroom` 确认 server 在跑；`kill -0 <PID>` alive。
> 之后 logout 不会再杀。

---

## 2. Agent 客户端接入（任一机器）

### 2.1 openwriter 接入（Linux GUI, PySide6）

```bash
# 1. 复制 adapter 到 workspace
mkdir -p <openwriter workspace>/_tools/chatroom
cp examples/openwriter-plugin/chatroom_adapter.py <openwriter workspace>/_tools/chatroom/adapter.py

# 2. register channel（绑当前 session 的 bound_session_id）
# 在 openwriter GUI 里调：
register(
    name="chatroom", type="channel",
    spec={
        "adapter_path": "_tools/chatroom/adapter.py",
        "auth_type": "none",
        "description": "chatroom-mcp 通道 (@openwriter 唤醒本 session)",
    },
)

# 3. 重启 openwriter（main.py 启动时才挂 bridge）
```

#### 2.1.1 踩坑 #1: bound_session_id 重启后失锚

`register` 写 sid 到 `_system/register/_schema.yaml`，但 adapter `__init__` 时从 schema 读这个 sid，缓存到 `self.bound_session_id`。
**问题**：register 时 adapter 实例立刻构造，此时 schema 的 sid 写入可能还没刷盘；或 restart 后 adapter 重新构造但 sid 仍在旧 schema 里——总之 bound_session_id 经常空。
**症状**：`status(channel="chatroom")` 返回 `last_error="no bound_session_id (见 _schema.yaml)"`。
**修复**：openwriter 重启时 `ChannelManager.recover` 用 `make_handler(merge=True)` 保留 schema 里的 sid，adapter 重新读，**session 仍在线**。
**只需重启 openwriter**，无需手动 register。

#### 2.1.2 踩坑 #2: 心跳回滚

如果 handshake 不传 `machine`，30s 后心跳把 server 上 active_sid 又刷回去。

修复：adapter handshake 加 `machine` 参数（hostname 或 `CHAT_MACHINE` env），server 端持久化到 `hosts[].machine`。

### 2.2 opencode 接入（Windows / Linux CLI, MCP client）

参考 `examples/opencode-plugin/chatroom-bridge.ts`：

1. 复制 `chatroom-bridge.ts` 到 opencode 的 `.opencode/plugin/` 目录
2. opencode 重启加载

#### 2.2.1 踩坑 #3: PULL race — sessionIdsFor 必须列 ALL

opencode Desktop 多实例（每项目目录一个）架构，bridge 用 `session.list()` 无参只列**当前 project** 的 3-12 个 session。
**症状**：`@opencode` 消息被 9 个实例全部判 "not mine"，dedup-skip 丢弃。
**复现**：msg-0022/0024/0038/0045 4 次丢消息。
**修复**：`sessionIdsFor` 改用 `session.list({query:{directory:""}})` 列**全部** session（含 CLI 会话，CLI 会话的 directory 字段是空串）。

> 不要用 PUSH 修复逻辑（改 active_sid / sid_invalid 回退）去修 PULL 问题。

#### 2.2.2 踩坑 #4: handshake 上报 fallback

CLI 启动时 `OPENCODE_SESSION_ID` 空，bridge 默认 fallback `"opencode-bobo-001"`。
**症状**：server 端 active_sid 是 fallback 假值，跟真 CLI sid 失锚。
**修复**：`bound_session_id = OPENCODE_SESSION_ID ?? active-marker ?? fallback`。
否则 30s heartbeat 反复覆盖 server.active_sid 假值。

### 2.3 通用接入要点

- **MCP 端点**：`<server>/mcp/`（**不是** `/api/`）— chatroom handshake / pull / post 都走 MCP
- **CHAT_URL / CHATROOM_HTTP**：adapter/bridge 用 `http://127.0.0.1:7777`（同机）。跨机调用要 LAN IP，但 server 默认不开 DNS rebinding 保护（实测通）
- **HEARTBEAT_S=0**：关心跳（pull-only agent 不需要在线状态）

---

## 3. 跨机验证脚本

`scripts/verify_connectivity.{sh,bat}` 已配默认 IP `192.168.31.127`（你自己机器的实际 IP）。6 步全过 = LAN 联通：

```
TCP OK / GET / 200 / GET /api/rooms 200 / GET /api/messages 200 / POST /api/post 200 / POST /mcp/ 200
```

改默认 IP：编辑脚本里的 `HOST="${1:-<your IP>}"`（bash）或 `set "HOST=%~1"`（bat）。

---

## 4. 文件传输 (B feature)

`POST /api/files`（multipart: `file`）→ 返回 `{file_id, name, size, content_type, url}`。
`GET /api/files/{file_id}` 下载。
`DELETE /api/files/{file_id}` 删除。

消息 `attachments: [{file_id, name, size, content_type}]` 引用文件。

- 默认 cap 50MB（env `CHATROOM_MAX_FILE_BYTES` 覆盖）
- `file_id = f-<16 hex>` (64-bit, 防枚举 — 仍 LAN 假设)
- server 端校验 attachments 存在性 + 用文件实际 size 覆盖客户端传的 size

---

## 5. 多 agent 世界状态注入（v0.3+ 进行中）

注入块格式（bridge 端实现）：

```
[chatroom-world]
me: opencode (bound=ses_xxx, machine=opencode-Win11-bobo)
online:
  - openwriter: online sid=ses_cee4... machine=openwriter-bobo-T300LA
  - workbuddy: ...
[chatroom @opencode] id=msg-xxx from=human to=[opencode, openwriter] type=finding
  subject: ...
  body...
```

`machine` 字段来自 server `handshake(name, sid, machine=...)`，持久化在 `_sessions.json` 的 `hosts[].machine`。
`to` 字段支持 list[str]（多目标），back-compat 单 str。
adapter `_is_for_me` 遍历 `to[]`，命中任一即注入。

---

## 6. 撞车规避（多 agent 同改 chatroom-mcp）

**3 步协议**：
1. **空间隔离**（默认不撞）：opencode → `src/chatroom/` + `examples/opencode-plugin/`；openwriter → `workspace/_tools/chatroom/adapter.py`
2. **时间隔离**：改前在 chatroom 发 `[WIP] 改 X, ETA 30min`；完成后 `[DONE] PR #N push`
3. **冲突检测**：push 前 `git fetch`，看 `origin/main..HEAD` 空=ff，1 commit=可能重叠

---

## 7. 已知坑位（汇总）

| 坑 | 表现 | 修复 |
|---|---|---|
| **mcp 2.x FastMCP rename** | ModuleNotFoundError on startup | `pip install 'mcp<2'` |
| **KillUserProcesses=yes** | logout 后 server 死 | 改 `/etc/systemd/logind.conf` + `restart systemd-logind` |
| **adapter bound_session_id 空** | `last_error="no bound_session_id"` | 重启 openwriter，ChannelManager.recover 用 merge=True |
| **PULL race (opencode 多实例)** | `@opencode` 消息全 not-mine skip | `sessionIdsFor` 用 `query.directory=""` 列 ALL |
| **handshake fallback 假 sid** | active_sid 30s 被覆盖回 fallback | bridge 上报真 sid (`OPENCODE_SESSION_ID ?? marker ?? fallback`) |
| **encode GBK / PowerShell 默认编码** | 中文变 `?` | curl `--data-binary` 显式 UTF-8 / browser GUI |
| **git 命令 "找不到"** | `git: 未找到命令` 但 `/usr/bin/git` 存在 | bash hash 缓存问题，`hash -r` 或 `/usr/bin/git` 直接调 |
| **workspace 不在 git 监控** | 改 adapter.py 不进 commit | workspace 跟 chatroom-mcp repo 分开；adapter 改动不需 commit，靠 openwriter 重启加载 |

---

## 8. 速查

```bash
# 服务端状态
ps aux | grep chatroom
ss -tlnp | grep 7777
curl -s http://127.0.0.1:7777/api/sessions | python3 -m json.tool

# adapter 状态（在 openwriter GUI 里）
status(channel="chatroom")    # bound_session_id, online, last_id, last_error

# Git 协作
git fetch origin
git checkout -B main origin/main
git checkout -B feature/<name> origin/main
git push origin feature/<name>

# 系统治本
grep KillUserProcesses /etc/systemd/logind.conf
sudo systemctl restart systemd-logind
```

