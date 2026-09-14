"""Receives chatroom push notifications and logs them to a file."""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import threading

LOG = Path(r"C:\dev\test-chat\callback.log")


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n).decode("utf-8")
        try:
            data = json.loads(body)
            subject = data.get("message", {}).get("subject", "")
            sender = data.get("message", {}).get("from", "")
            with LOG.open("a", encoding="utf-8") as f:
                f.write(f"CALLBACK: from={sender} subj={subject}\n")
        except Exception as e:
            with LOG.open("a", encoding="utf-8") as f:
                f.write(f"CALLBACK: parse error {e}\n")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9999
    LOG.write_text(f"# callback receiver listening on {port}\n", encoding="utf-8")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
