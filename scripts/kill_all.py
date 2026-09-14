"""kill_all.py - kill all chatroom-mcp / agent processes."""
import re
import subprocess

TARGETS = ["chatroom.server", "openwriter_agent", "bobo_bridge", "bobo_agent",
           "bobo_listener", "callback_receiver"]

out = subprocess.run(
    ["wmic", "process", "where", "name='python.exe'", "get", "ProcessId,CommandLine"],
    capture_output=True, text=True, errors="replace",
)
killed = []
for line in out.stdout.splitlines():
    line = line.strip()
    if not line or "ProcessId" in line:
        continue
    m = re.search(r"(\d+)\s*$", line)
    if not m:
        continue
    if any(t in line.lower() for t in TARGETS):
        subprocess.run(["taskkill", "/F", "/PID", m.group(1)], capture_output=True)
        killed.append(m.group(1))
print("killed:", killed if killed else "(none)")
