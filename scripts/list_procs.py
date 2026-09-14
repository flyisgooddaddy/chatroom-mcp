"""list_procs.py - show which python processes are chatroom-related."""
import re
import subprocess

TAGS = {
    "chatroom.server": "SERVER",
    "openwriter_agent": "OPENWRITER AGENT",
    "bobo_bridge": "bobo_bridge(old)",
    "bobo_agent": "bobo_agent(old)",
    "callback_receiver": "callback",
}

out = subprocess.run(
    ["wmic", "process", "where", "name='python.exe'", "get", "ProcessId,CommandLine"],
    capture_output=True, text=True, errors="replace",
)
found = 0
for line in out.stdout.splitlines():
    line = line.strip()
    if not line or "ProcessId" in line:
        continue
    m = re.search(r"(\d+)\s*$", line)
    if not m:
        continue
    pid = m.group(1)
    low = line.lower()
    for key, tag in TAGS.items():
        if key in low:
            print(f"  [{tag}] PID {pid}")
            found += 1
            break
if not found:
    print("  (no chatroom processes running)")
