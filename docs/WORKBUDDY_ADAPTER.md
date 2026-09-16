# WorkBuddy / CodeBuddy → chatroom-mcp integration

> "How does an agent framework that has **no inbound HTTP push endpoint**
> receive a `@`-mention in chatroom-mcp, and wake up to reply?"

This document is the answer, using **WorkBuddy** (a Tencent CodeBuddy desktop
product) as the worked example. The same pattern generalizes to any agent
framework that lacks a callback URL.

Companion code: [`examples/workbuddy_adapter.py`](../examples/workbuddy_adapter.py).

---

## 0. The honest answer in one sentence

> The chatroom **stores every message** regardless of push success, so a
> **pull-only** agent that registers with an empty `callback_url` still
> receives `@`-mentions via `chatroom_pull`; the agent framework then wakes
> itself through its **own** native wake path (for WorkBuddy: `codebuddy
> --resume <sessionId>`), and posts the reply back with `chatroom_post`.

Three pieces, none of them a hack:

1. **chatroom-mcp**: `callback_url` is now optional on `chatroom_handshake`.
   Push is best-effort; pull is the source of truth.
2. **agent-side thin adapter**: poll → wake → post. Lives **outside** the
   agent process and uses the agent framework's own external wake command.
3. **the wake command**: framework-specific (WorkBuddy → `codebuddy --resume`,
   opencode → its per-session `prompt_async` port, …). This is the **only**
   non-universal piece.

---

## 1. Why this design

### Why not push?

chatroom's `@` mechanism: when a message has `to: "<agent>"`, the server looks
up the agent's active host's `callback_url` and `POST`s the payload there. If
the callback is missing or unreachable, the push is silently dropped —
**but the message is already persisted**, so any agent that pulls will see it.

`callback_url` requires the agent to expose a local HTTP endpoint. opencode
exposes one per session (the `127.0.0.1:<port>/push` its bridge registers as
the callback), so push works for it. WorkBuddy exposes **none** — its inbound
flow is the Tencent Centrifugo backend (cloud), not a local port — so push
*cannot* reach WorkBuddy. Pretending we can fix that from chatroom's side
would mean inventing a WorkBuddy-only hook in the server.

### Why not "chatroom figures out how to inject into each agent"?

Because "how do I inject a prompt into a specific session" is irreducibly
host-specific (it needs in-process access, or the framework's own external
API). chatroom cannot discover it. The two viable patterns are:

- **pull + thin adapter** ← this document. Universal on the chatroom side;
  one ~100-line script per framework.
- **push to a URL the agent controls** ← what opencode does (the bridge
  exposes `/push`). Requires the agent framework to already have such a URL.

### Why the adapter runs **outside** WorkBuddy?

WorkBuddy's session state lives in the WorkBuddy daemon. From outside, you
can't directly drive a turn — but WorkBuddy does ship a `codebuddy` CLI
(built into the app), which supports `codebuddy --resume <sessionId> -p "<msg>"`
to resume a session headlessly and stream a turn. That CLI is the bridge.
Putting the adapter inside WorkBuddy would require patching the app bundle;
the CLI path needs no patching.

---

## 2. The chatroom-mcp change (one-line)

`sessions.handshake` no longer requires `callback_url`:

- `src/chatroom/sessions.py`: the `if not callback_url: raise` was removed.
  `callback_url` defaults to `""`.
- `src/chatroom/server.py`: the `chatroom_handshake` tool signature makes it
  optional; the description states it's only needed if the agent exposes a
  push endpoint.
- `tests/test_sessions.py::test_handshake_allows_empty_callback_url`
  replaces the old mandatory-callback test.

Push behavior is unchanged: `_resolve_push_target` already treats an empty
`callback_url` as "no push possible" and falls through silently.

---

## 3. The adapter (`examples/workbuddy_adapter.py`)

Long-running Python process. Lifecycle:

```
                       every WB_POLL_INTERVAL
                                │
   chatroom_handshake(name=workbuddy, bound_session_id=<sid>, callback_url="")
                                │
   chatroom_pull(limit=50)  ────┼──►  for each msg with to == workbuddy and not yet processed:
                                │         wake = codebuddy --resume <sid> -p "<prompt>" ...
                                │         reply = wake.stdout
                                │         chatroom_post(msg {type=ack, in_reply_to=..., to=sender, body=reply})
                                │
                                └──►  asyncio.sleep(POLL_INTERVAL)
```

Key design points:

| concern | choice |
|---|---|
| transport | streamable HTTP via the official `mcp` SDK client |
| pull cadence | 3 s default |
| processed-set persistence | `WB_PROCESSED_FILE` (default `~/.workbuddy/wb_processed.json`) so restarts don't re-respond |
| wake subprocess encoding | capture as bytes, decode `utf-8` with `errors="replace"` (Windows `text=True` falls back to the console codepage and drops non-gbk bytes) |
| tool restriction | `--tools ""` so the model replies text-only instead of trying to use MCP tools that error as "Tool Not Found" in a text-only context |
| max-turns | 3 (resume + answer; without `--tools ""` 1 is too few) |
| critical env | `CODEBUDDY_CONFIG_DIR` must point at the agent's data dir, otherwise the CLI defaults to its own brand dir and can't see the session |

The wake command, in the form the example runs:

```
node codebuddy.js -r <sessionId> -p "<prompt>" -y --print \
    --tools "" --max-turns 3
```

with env `CODEBUDDY_CONFIG_DIR=~/.workbuddy`.

---

## 4. The two "almost-impossible" gotchas (and their fixes)

### 4.1 `No conversation found with session ID`

Symptom: `codebuddy -r <sid>` returns "No conversation found" even though the
transcript exists at `~/.workbuddy/projects/<hashed-cwd>/<sid>.jsonl`.

Cause: the bare CLI defaults to `~/.codebuddy` (the CodeBuddy brand dir); it
never looks at `~/.workbuddy` unless its data dir is explicitly told.

Fix: `CODEBUDDY_CONFIG_DIR=<.workbuddy>`. Also use `dist/codebuddy.js`
directly — `bin/codebuddy` routes `--print` to `codebuddy-headless.js`, which
is CodeBuddy-branded and has zero `.workbuddy` references.

### 4.2 Subprocess stdout gets silently dropped

Symptom: `codebuddy --print` produces output, but `proc.stdout.strip()` is
empty / raises `AttributeError: NoneType has no attribute 'strip'`.

Cause: Python's `subprocess.run(..., text=True)` on Windows uses the console
codepage (cp936 / GBK) to decode. A single non-GBK byte in the child's stdout
crashes `_readerthread`, which silently truncates the buffer and returns
`None`.

Fix: `capture_output=True` (no `text=True`) → `proc.stdout` is bytes → decode
manually with `utf-8` + `errors="replace"`. The example does this.

---

## 5. End-to-end demo (what was tested)

| step | result |
|---|---|
| human posts `@workbuddy ...` | msg-N, `to: "workbuddy"` |
| adapter polls, picks it up | `[adapter] @wake: msg-N ...` |
| `codebuddy --resume` runs | model produces a turn on the live session transcript |
| adapter posts `type=ack, in_reply_to=msg-N` | msg-N+M, `from: workbuddy`, `to: human` |
| chatroom pull from another client | sees msg-N+M with the model's reply |

Verified after the full sequence was restarted: handshakes persist in
`~/.chatroom/comms/_sessions.json`, the adapter re-registers on next start,
processed-id set prevents double-responses, no MCP channel on the WorkBuddy
side needed.

---

## 6. Adapting this pattern to another agent framework

The adapter is intentionally small and framework-agnostic in structure. To
port it:

1. Replace `wake_session()` with whatever command your agent exposes to
   resume and run a turn. opencode exposes a `prompt_async` HTTP endpoint,
   so `wake_session` becomes an `httpx.post(...)` to that URL.
2. Replace `NODE_EXE` / `CODEBUDDY_JS` / `CODEBUDDY_CONFIG_DIR` with the
   framework's wake inputs (or remove if the framework speaks HTTP).
3. Adjust `--max-turns` and tool restrictions to match your framework's
   defaults.
4. `chatroom_handshake` stays the same: `name`, `bound_session_id`, empty
   `callback_url`.

The chatroom side never changes. The only per-framework part is one function.

---

## 7. Troubleshooting

| symptom | fix |
|---|---|
| `No conversation found with session ID` from codebuddy | set `CODEBUDDY_CONFIG_DIR=~/.workbuddy`; call `dist/codebuddy.js` directly (not `bin/codebuddy`) |
| subprocess stdout is empty / NoneType on `strip()` | capture bytes (no `text=True`) and decode utf-8 manually |
| adapter re-responds to old `@`s after restart | ensure `wb_processed.json` is being loaded/saved; path = `~/.workbuddy/wb_processed.json` by default |
| `SyntaxWarning: invalid escape sequence '\P'` | the docstring/raw strings in the example are `r"""..."""` for this reason |
| WorkBuddy UI doesn't show the injected turn | expected — CLI resume writes to the transcript, but the desktop daemon→renderer channel doesn't see external writes. Reopen the session in the WorkBuddy UI to see it; or use the `codebuddy --serve --open` Web UI at `http://127.0.0.1:18789/` (see `wb-inject-mcp/server.py`) |
| `Max turns (1) exceeded` from codebuddy | add `--max-turns 3` (or higher) to the wake command, or `--tools ""` to keep it text-only |