/**
 * chatroom-bridge: deliver chatroom @-mentions into a user-chosen opencode session.
 *
 * Polls the chatroom for messages routed to `opencode`, injects each as a user
 * turn into the bound opencode session (which carries the full conversation
 * context) via `client.session.prompt`. The injected prompt tells the model to
 * reply using its OWN `chatroom_post` MCP tool — so the answer is produced
 * in-context and posted by the model directly, without the plugin trying to
 * capture/echo the assistant text (which was fragile).
 *
 * Session binding protocol (user-decided, not auto-guessed):
 *   - reportHost(): POST /api/agent/opencode/host  — publish the selectable
 *     host conversations + the current bound session id. This also acts as the
 *     REST handshake, so the agent shows up in the chatroom sidebar.
 *   - pollCommands(): GET /api/agent/opencode/commands — pull user-issued
 *     bind|create|unbind commands and execute them in-process.
 *   - ack(): POST /api/agent/opencode/commands/ack — confirm the new binding.
 *
 * Deliberately dependency-free (no node:fs / node:path, and NO @opencode-ai/plugin
 * import — importing it makes opencode try to install it and fail to resolve,
 * which breaks plugin loading).
 *
 * Auto-loaded from .opencode/plugin/.
 */

const CHAT_URL = "http://127.0.0.1:7777"
const AGENT = "opencode"
const ROOM = "main"
const POLL_MS = 3_000
const CMD_POLL_MS = 2_000
const REPORT_MS = 10_000

export default (async ({ client, directory }) => {
  let lastId = ""        // chatroom watermark: highest msg id seen
  let primed = false     // first poll only sets the watermark (skip old @backlog)
  let busy = false
  const seen = new Set<string>()
  let boundSessionId: string | null = null   // user-bound host session (null => auto)
  let hostSessions: any[] = []

  const log = (...a: unknown[]) => console.log("[chatroom-bridge]", ...a)
  const norm = (p: string) => (p || "").replace(/\\/g, "/").replace(/\/+$/, "")

  function postJson(url: string, body: unknown): Promise<Response> {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
  }

  async function listSessions(): Promise<any[]> {
    try {
      const res: any = await client.session.list()
      return Array.isArray(res) ? res : res?.data || []
    } catch (e) {
      log("listSessions failed", e)
      return []
    }
  }

  // Already answered? (any opencode message whose in_reply_to === msgId)
  async function hasExistingReply(msgId: string): Promise<boolean> {
    try {
      const r = await fetch(`${CHAT_URL}/api/messages?room=${encodeURIComponent(ROOM)}&limit=200`)
      const data: any = await r.json()
      const list: any[] = Array.isArray(data) ? data : data?.messages || []
      return list.some((m: any) => m?.from === AGENT && m?.in_reply_to === msgId)
    } catch {
      return false
    }
  }

  async function findCurrentSessionID(): Promise<string | null> {
    try {
      const sessions = await listSessions()
      if (!sessions.length) return null
      const dir = norm(directory)
      const inDir = sessions.filter(
        (s: any) => norm(s.directory) === dir || norm(s.worktree) === dir,
      )
      const pool = inDir.length ? inDir : sessions
      pool.sort((a: any, b: any) => (b.timeUpdated || 0) - (a.timeUpdated || 0))
      return pool[0]?.id || null
    } catch (e) {
      log("findCurrentSessionID failed", e)
      return null
    }
  }

  // Resolve the session to inject into: a user-bound session always wins,
  // otherwise fall back to the newest session in this directory.
  async function resolveSessionId(): Promise<string | null> {
    if (boundSessionId) return boundSessionId
    return await findCurrentSessionID()
  }

  // Only the instance whose session is the globally-newest injects auto replies.
  async function isPrimaryInstance(): Promise<boolean> {
    try {
      const mine = await findCurrentSessionID()
      if (!mine) return true
      const sessions = await listSessions()
      if (!sessions.length) return true
      let newest = sessions[0]
      for (const s of sessions) {
        if ((s?.timeUpdated || 0) > (newest?.timeUpdated || 0)) newest = s
      }
      return newest?.id === mine
    } catch {
      return true
    }
  }

  // ---- session binding protocol ----

  async function reportHost(): Promise<void> {
    try {
      const sessions = await listSessions()
      hostSessions = sessions.map((s: any) => ({
        id: s.id,
        title: s.title || "",
        directory: s.directory || s.worktree || "",
        timeUpdated: s.timeUpdated || 0,
      }))
      await postJson(`${CHAT_URL}/api/agent/${AGENT}/host`, {
        host_sessions: hostSessions,
        bound_session_id: boundSessionId,
      })
    } catch (e) {
      log("reportHost failed", e)
    }
  }

  async function ack(newBound: string | null): Promise<void> {
    try {
      await postJson(`${CHAT_URL}/api/agent/${AGENT}/commands/ack`, {
        bound_session_id: newBound,
        host_sessions: hostSessions,
      })
    } catch (e) {
      log("ack failed", e)
    }
  }

  async function createSession(cmd: any): Promise<string | null> {
    try {
      const c: any = (client as any).session
      if (typeof c?.create === "function") {
        const created = await c.create({
          title: cmd?.title || "chatroom",
          directory: cmd?.directory || directory,
        })
        if (created?.id) return created.id
      }
    } catch (e) {
      log("createSession failed", e)
    }
    log("host has no session.create; falling back to newest in directory")
    return await findCurrentSessionID()
  }

  async function executeCommand(cmd: any): Promise<void> {
    const op = String(cmd?.op || "")
    let newBound = boundSessionId
    if (op === "bind") {
      newBound = cmd?.session_id || null
    } else if (op === "create") {
      newBound = await createSession(cmd)
    } else if (op === "unbind") {
      newBound = null
    } else {
      log("unknown command op, skip:", op)
      return
    }
    boundSessionId = newBound
    log("binding ->", newBound || "(auto)")
    await ack(newBound)
  }

  async function pollCommands(): Promise<void> {
    try {
      const r = await fetch(`${CHAT_URL}/api/agent/${AGENT}/commands`)
      const data: any = await r.json()
      const cmds: any[] = data?.commands || []
      for (const cmd of cmds) await executeCommand(cmd)
    } catch (e) {
      log("pollCommands failed", e)
    }
  }

  // ---- @ delivery ----

  async function handle(msg: any) {
    busy = true
    try {
      const mid = String(msg.id || "")
      if (await hasExistingReply(mid)) {
        log("already replied elsewhere, skip:", mid)
        return
      }
      const sid = await resolveSessionId()
      if (!sid) {
        log("no session to inject into, skip:", mid)
        return
      }
      if (!boundSessionId && !(await isPrimaryInstance())) {
        log("non-primary instance, stay silent:", mid)
        return
      }
      const body = msg.body || msg.subject || ""
      const to = msg.from || "human"
      const room = msg.room || ROOM
      const prompt =
        `[来自聊天室的 @] ${msg.from} 对我说：\n${body}\n\n` +
        `请你结合当前对话的上文给出回复，并把这个回复发回聊天室——` +
        `使用你的 chatroom_post 工具，参数 msg 为：` +
        `{ "from": "${AGENT}", "to": "${to}", "type": "finding", "room": "${room}", ` +
        `"in_reply_to": "${mid}", "subject": "<回复的前80字>", "body": "<完整回复>" }`
      await client.session.prompt({
        path: { id: sid },
        body: { parts: [{ type: "text", text: prompt }] },
      })
      // The model replies via its own chatroom_post tool; nothing to capture here.
    } catch (e) {
      log("handle failed", e)
    } finally {
      busy = false
    }
  }

  async function tick() {
    if (busy) return
    try {
      // Store-reset tolerance: "clear" empties the store and message ids restart
      // low (msg-0001...). Our watermark would otherwise sit at an old high id
      // and `after=<old high>` would never match the new low ids -> @ goes deaf.
      // Peek the current newest id and, if it went BACKWARD vs our watermark,
      // re-prime so we keep hearing future @.
      let newest = ""
      try {
        const pr = await fetch(
          `${CHAT_URL}/api/messages?room=${encodeURIComponent(ROOM)}&limit=1`,
        )
        const pd: any = await pr.json()
        const pl: any[] = Array.isArray(pd) ? pd : pd?.messages || []
        newest = pl[0]?.id ?? ""
      } catch {}
      if (lastId && (!newest || newest < lastId)) {
        log("store reset detected (newest=", newest, ", watermark=", lastId, ") re-prime")
        lastId = "" // full re-sync next poll so even the first post-reset @ is caught
      }

      const q = lastId ? `&after=${encodeURIComponent(lastId)}` : ""
      const r = await fetch(`${CHAT_URL}/api/messages?room=${encodeURIComponent(ROOM)}&limit=50${q}`)
      const data: any = await r.json()
      const list: any[] = Array.isArray(data) ? data : data?.messages || []
      if (!list.length) return

      lastId = list[list.length - 1]?.id ?? lastId
      if (!primed) {
        primed = true
        return
      }

      for (const m of list) {
        if (m.from === AGENT || m.from === "opencode-bridge") continue
        const isForMe =
          m.to === AGENT ||
          /^@opencode\b/.test(m.subject || "") ||
          /^@opencode\b/.test(m.body || "")
        if (!isForMe) continue
        const key = String(m.id)
        if (seen.has(key)) continue
        seen.add(key)
        log(" @ received:", key, "—", (m.subject || "").slice(0, 30))
        await handle(m)
      }
    } catch (e) {
      // transient
    }
  }

  log(`polling ${CHAT_URL} for @${AGENT} every ${POLL_MS}ms`)
  await reportHost()
  await pollCommands()
  const t = setInterval(tick, POLL_MS)
  const c = setInterval(pollCommands, CMD_POLL_MS)
  const h = setInterval(reportHost, REPORT_MS)
  tick()
  const stop = () => {
    clearInterval(t)
    clearInterval(c)
    clearInterval(h)
  }
  try {
    process.once("exit", stop)
    process.on("SIGINT", stop)
  } catch {}
  return {}
})