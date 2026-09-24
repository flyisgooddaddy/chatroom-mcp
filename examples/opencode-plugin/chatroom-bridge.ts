import type { Plugin } from "@opencode-ai/plugin"
import { appendFile, mkdir, open, readdir, readFile, rm, writeFile } from "node:fs/promises"
import { join } from "node:path"
import { homedir } from "node:os"

const CHATROOM_HTTP = process.env.CHATROOM_HTTP ?? "http://192.168.31.127:7777"
const MCP_URL = `${CHATROOM_HTTP}/mcp/`
const AGENT_NAME = "opencode"
const AGENT_ALIASES = ["opencode", "opencode-desktop", "main-agent", "opencode-bobo-001"]
const ROOM = "main"
const HEARTBEAT_MS = 30_000
const POLL_TIMEOUT_S = 25
const SRV_TIMEOUT_MS = 8_000
const PROMPT_TIMEOUT_MS = 180_000

const STATE_DIR = join(homedir(), ".local", "share", "opencode")
const LOG_FILE = join(STATE_DIR, "chatroom-bridge.log")
const ACTIVE_FILE = join(STATE_DIR, "chatroom-bridge.active")
const LASTINJECT_FILE = join(STATE_DIR, "chatroom-bridge.lastinject")

function withTimeout<T>(p: Promise<T>, ms: number, label: string): Promise<T> {
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error(`${label} timeout after ${ms}ms`)), ms)
    p.then((v) => { clearTimeout(t); resolve(v) }, (e) => { clearTimeout(t); reject(e) })
  })
}

async function flog(line: string) {
  const ts = new Date().toISOString()
  try { await appendFile(LOG_FILE, `${ts} ${line}\n`, "utf8") } catch {}
}

// Exactly-once across every plugin instance (OpenCode Desktop 1.18 runs one
// instance per project dir) and across restarts.
//
// An in-memory Set does NOT work here: each of the N instances loads the file
// once at startup and then trusts its own cache, so all N think they are first.
// Use an atomic exclusive-create instead — `open(path, "wx")` is O_CREAT|O_EXCL
// at the OS level, so exactly one caller wins even across processes.
const CLAIM_DIR = join(STATE_DIR, "chatroom-bridge.claims.d")

async function claimOnce(id: string): Promise<boolean> {
  try {
    await mkdir(CLAIM_DIR, { recursive: true })
    const fh = await open(join(CLAIM_DIR, id), "wx")
    await fh.close()
    return true
  } catch (e: any) {
    if (e?.code === "EEXIST") return false
    return true // never block delivery on lock errors
  }
}

// Drop claim files older than 7 days, at startup only.
async function pruneClaims() {
  try {
    const cutoff = Date.now() - 7 * 24 * 3600 * 1000
    const names = await readdir(CLAIM_DIR)
    for (const n of names) {
      const p = join(CLAIM_DIR, n)
      try {
        const st = await (await open(p, "r")).stat()
        if (st.mtimeMs < cutoff) await rm(p, { force: true })
      } catch {}
    }
  } catch {}
}

let mcpSessionId: string | null = null

async function mcpInitialize(): Promise<void> {
  const res = await fetch(MCP_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json, text/event-stream" },
    body: JSON.stringify({
      jsonrpc: "2.0", id: 1, method: "initialize",
      params: { protocolVersion: "2025-03-26", capabilities: {}, clientInfo: { name: "opencode-chatroom-bridge", version: "0.3.0" } },
    }),
  })
  if (!res.ok) throw new Error(`mcp init ${res.status}`)
  const sid = res.headers.get("mcp-session-id")
  if (sid) mcpSessionId = sid
}

async function mcpCallTool(name: string, args: Record<string, unknown>): Promise<any> {
  if (!mcpSessionId) await mcpInitialize()
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "application/json, text/event-stream",
  }
  if (mcpSessionId) headers["Mcp-Session-Id"] = mcpSessionId
  const res = await fetch(MCP_URL, {
    method: "POST", headers,
    body: JSON.stringify({ jsonrpc: "2.0", id: Date.now(), method: "tools/call", params: { name, arguments: args } }),
  })
  if (!res.ok) {
    if (res.status === 400 || res.status === 404) {
      mcpSessionId = null
      await mcpInitialize()
      return mcpCallTool(name, args)
    }
    throw new Error(`mcp ${name} ${res.status}`)
  }
  const text = await res.text()
  let payload: any
  if (text.startsWith("{")) payload = JSON.parse(text)
  else {
    const dataLine = text.split("\n").find((l) => l.startsWith("data: "))
    if (!dataLine) throw new Error(`mcp ${name}: no data line`)
    payload = JSON.parse(dataLine.slice(6))
  }
  if (payload.error) throw new Error(`mcp ${name}: ${JSON.stringify(payload.error)}`)
  return payload.result
}

async function handshake() {
  // Report the REAL active session as our bound sid (not the fallback). This
  // lets chatroom's server-side active_sid / future recent_sids reflect the
  // session we actually run in, so agent-side fallback has real data.
  // Order: explicit OPENCODE_SESSION_ID > our active-marker > legacy fallback.
  let bound = process.env.OPENCODE_SESSION_ID
  if (!bound) {
    const marker = await readActive()
    bound = marker ?? "opencode-bobo-001"
  }
  await mcpCallTool("chatroom_handshake", {
    name: AGENT_NAME,
    bound_session_id: bound,
    callback_url: "",
    session_name: "opencode-desktop",
  })
}

async function pullMessages(after: string) {
  const url = `${CHATROOM_HTTP}/api/messages/poll?room=${encodeURIComponent(ROOM)}&after=${encodeURIComponent(after)}&timeout=${POLL_TIMEOUT_S}`
  const res = await fetch(url, { headers: { Accept: "application/json" } })
  if (!res.ok) throw new Error(`poll ${res.status}`)
  const data: any = await res.json()
  return ((data?.messages ?? []) as any[])
    .filter((m) => m && m.id && m.id > after)
    .map((m) => ({ id: String(m.id), msg: m }))
}

function isTargeting(msg: any): boolean {
  // Skip messages we sent ourselves — prevents echo loops when our own
  // ack/reply contains @<self> in the subject (e.g. "re: @opencode ...")
  const from = String(msg?.from ?? "")
  if (AGENT_ALIASES.includes(from)) return false
  const to = String(msg?.to ?? "")
  if (AGENT_ALIASES.some((a) => to === a || to.includes(a))) return true
  const subj = String(msg?.subject ?? "")
  if (AGENT_ALIASES.some((a) => subj.includes(`@${a}`))) return true
  const body = String(msg?.body ?? "")
  if (AGENT_ALIASES.some((a) => body.includes(`@${a}`))) return true
  return false
}

// Cross-instance "active session" tracking.
//
// session.list() turned out to be per-instance scoped (each OpenCode Desktop
// project instance sees only its own project's sessions), so "globally newest
// session" cannot be computed from it. Instead every instance watches the
// server's event bus and records the most recently active session id to a
// shared file. The event bus is server-global, so this converges on the session
// the user is actually using. If it is per-instance, the union still picks the
// most recent activity, which is what we want.
let lastActiveWrite = 0
let lastActiveSid = ""

async function recordActive(sessionId: string) {
  if (!sessionId) return
  const now = Date.now()
  if (sessionId === lastActiveSid && now - lastActiveWrite < 1000) return
  lastActiveSid = sessionId
  lastActiveWrite = now
  try { await writeFile(ACTIVE_FILE, `${now} ${sessionId}`, "utf8") } catch {}
}

async function readActive(): Promise<string | null> {
  try {
    const raw = await readFile(ACTIVE_FILE, "utf8")
    const parts = raw.trim().split(/\s+/)
    const sid = parts[1] || ""
    // validate: concurrent writers could interleave; never trust a malformed id
    return sid.startsWith("ses_") ? sid : null
  } catch { return null }
}

// Our own session.prompt() injection creates a role=user message, which the
// event hook would otherwise mistake for the human speaking — pinning the
// "active session" marker to the injected session forever (self-reinforcing
// loop that sent every later @-mention to the wrong session). Record each
// injection here and have the hook ignore the echo for a short window.
const INJECT_ECHO_MS = 20_000

async function noteInjection(sid: string) {
  try { await writeFile(LASTINJECT_FILE, `${Date.now()} ${sid}`, "utf8") } catch {}
}

async function readLastInjection(): Promise<{ ts: number; sid: string } | null> {
  try {
    const raw = await readFile(LASTINJECT_FILE, "utf8")
    const [tsStr, s] = raw.trim().split(/\s+/)
    const ts = parseInt(tsStr, 10)
    if (s?.startsWith("ses_") && Number.isFinite(ts)) return { ts, sid: s }
  } catch {}
  return null
}

async function isOwnInjectionEcho(sid: string): Promise<boolean> {
  const li = await readLastInjection()
  return !!li && li.sid === sid && Date.now() - li.ts < INJECT_ECHO_MS
}

// Session ids visible to THIS plugin instance.
//
// We must list across ALL directories (directory:"") — NOT the bare default,
// which is per-project and EXCLUDES CLI/global sessions. That exclusion was the
// root cause of intermittent "not mine" drop: an @-mention targeting our active
// CLI session (directory="") never belonged to any project instance's bare
// session.list(), so everyone skipped it.
//
// Listing everything means multiple instances can claim the same marker. That's
// fine: claimOnce() (atomic exclusive-create) still guarantees exactly-once
// delivery, and the injection target is still the shared active-marker.
async function sessionIdsFor(client: any): Promise<Set<string> | null> {
  try {
    const res = await withTimeout(
      (client as any).session.list({ query: { directory: "" } }),
      SRV_TIMEOUT_MS, "session.list",
    )
    const list: any[] = res?.data ?? res ?? []
    if (!Array.isArray(list)) return null
    return new Set(list.map((s) => String(s?.id ?? "")).filter(Boolean))
  } catch {
    return null
  }
}

// Inject a chatroom @ message into the current opencode session as a normal
// user message. The model will see it alongside its full prior context, then
// call chatroom_post itself to reply. The plugin does NOT echo or scrape
// assistant text — that's §6's explicit instruction.
//
// Prompt is intentionally short: chatroom message body + a one-line hint
// about chatroom_post. No command-style instructions, no parameter dumps.
async function injectIntoSession(client: any, sessionId: string, msg: any): Promise<void> {
  const prompt =
    `[chatroom @${AGENT_NAME}] from=${msg.from ?? "?"} id=${msg.id}` +
    (msg.subject ? `  subject: ${msg.subject}\n` : "\n") +
    (msg.body ? `\n${msg.body}\n` : "") +
    (msg.in_reply_to ? `\n(in_reply_to: ${msg.in_reply_to})\n` : "") +
    `\n（chatroom @${AGENT_NAME} 收到新消息；如需回复，用 chatroom MCP 工具 chatroom_post，in_reply_to=${msg.id}）`

  try {
    // mark BEFORE prompting: the resulting role=user event can arrive almost
    // immediately, and the event hook must already know it is ours.
    await noteInjection(sessionId)
    const r = await withTimeout(
      (client as any).session.prompt({
        path: { id: sessionId },
        body: { parts: [{ type: "text", text: prompt }] },
      }),
      PROMPT_TIMEOUT_MS, "session.prompt",
    )
    await flog(`inject ok sid=${sessionId} data=${r?.data ? "present" : "absent"}`)
  } catch (e: any) {
    await flog(`inject failed sid=${sessionId}: ${e?.message ?? e}`)
  }
}

export const ChatroomBridge: Plugin = async ({ client, directory }) => {
  let lastId = ""
  let stopped = false
  let primed = false

  await flog(`=== chatroom-bridge started (dir=${directory ?? "?"}) ===`)

  pruneClaims().catch(() => {})
  handshake().catch((e: any) => flog(`initial handshake failed: ${e?.message ?? e}`))

  // heartbeat
  ;(async () => {
    while (!stopped) {
      try { await handshake(); await flog("heartbeat ok") }
      catch (e: any) { await flog(`heartbeat failed: ${e?.message ?? e}`) }
      await new Promise((r) => setTimeout(r, HEARTBEAT_MS))
    }
  })().catch(() => {})

  // long-poll + inject
  ;(async () => {
    while (!stopped) {
      try {
        const fresh = await pullMessages(lastId)
        if (fresh.length === 0) continue

        // Priming: on the very first successful poll after startup, advance the
        // watermark past all existing history WITHOUT injecting anything.
        // Otherwise every restart replays the whole room and triggers duplicate
        // model runs (matches the OpenWriter adapter's `_primed` behavior).
        if (!primed) {
          primed = true
          lastId = fresh[fresh.length - 1].id
          await flog(`primed at ${lastId} (skipped ${fresh.length} historical)`)
          continue
        }

        for (const { id, msg } of fresh) {
          lastId = id
          if (!isTargeting(msg)) continue

          // Target = the session where the human last spoke (marker written by
          // the event hook, with our own injection echoes excluded). Only the
          // instance that OWNS that session may inject — session.list() is
          // per-project scoped, so the owner is unique. Non-owners stay silent
          // (§6's 多实例收敛).
          //
          // No marker yet (nobody has typed in the GUI since the marker was
          // reset)? Skip rather than guess: injecting into a stale/forked
          // session is worse than dropping one message.
          const marker = await readActive()
          if (!marker) {
            await flog(`skip ${id}: no active marker (send a message in the opencode GUI first)`)
            continue
          }
          const mine = await sessionIdsFor(client)
          if (mine && mine.size > 0 && !mine.has(marker)) {
            await flog(`skip ${id}: active=${marker} not mine (${mine.size} sessions)`)
            continue
          }
          const target = marker

          // Exactly-once across instances + restarts (atomic exclusive create).
          if (!(await claimOnce(id))) {
            await flog(`dedup-skip ${id}`)
            continue
          }
          await flog(`HIT ${id} from=${msg.from} subj="${msg.subject}" target=${target}`)
          await injectIntoSession(client, target, msg)
        }
      } catch (e: any) {
        await flog(`poll loop: ${e?.message ?? e}`)
        await new Promise((r) => setTimeout(r, 5000))
      }
    }
  })().catch(() => {})

  let eventLogBudget = 30
  return {
    event: async ({ event }) => {
      try {
        const t = String((event as any)?.type ?? "")
        const p: any = (event as any)?.properties ?? {}

        // "Active session" signal = the user just SENT a message.
        //
        // Do NOT use session.status / session.diff / message.part.* — a
        // background session (e.g. one still streaming from an earlier run)
        // keeps emitting status events and would hijack the marker, sending
        // chatroom @-mentions to a session the user is not looking at. That
        // was the exact failure that broke @ delivery.
        if (t === "message.updated") {
          const info = p?.info ?? {}
          const sid = info?.sessionID ?? p?.sessionID
          const role = info?.role
          if (eventLogBudget > 0) {
            eventLogBudget--
            await flog(`event ${t} role=${role} sid=${typeof sid === "string" ? sid : "?"}`)
          }
          if (role === "user" && typeof sid === "string" && sid.startsWith("ses_")) {
            if (await isOwnInjectionEcho(sid)) {
              if (eventLogBudget > 0) { eventLogBudget--; await flog(`ignore own inject echo sid=${sid}`) }
            } else {
              await recordActive(sid)
            }
          }
        }
      } catch {}
    },
  }
}

export default ChatroomBridge