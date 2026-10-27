"""Stand-ins for what is outside the cluster: Slack's incoming webhook, and an
address an attacker owns.

ROLE=slack answers POST /services/... with "ok", as Slack does, and prints
the message it was given. ROLE=outside answers anything with 200 and prints
who asked for what, without the body: it is where a fooled agent would send
data, and the e2e run reads these lines to see whether anything got there.

Standard library only, on a pinned python:3.13-alpine.
"""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROLE = os.environ.get("ROLE", "outside")
MAX_BODY = 64 * 1024


def record(**line):
    line["at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    line["role"] = ROLE
    print(json.dumps(line, sort_keys=True), flush=True)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    sys_version = ""

    def log_message(self, *_):
        pass

    def answer(self, status, text):
        data = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def handle_any(self):
        if self.path == "/healthz":
            return self.answer(200, "ok")
        length = min(int(self.headers.get("Content-Length") or 0), MAX_BODY)
        raw = self.rfile.read(length) if length else b""
        if ROLE == "slack":
            if self.command != "POST" or not self.path.startswith("/services/"):
                return self.answer(404, "no_service")
            try:
                payload = json.loads(raw)
            except ValueError:
                return self.answer(400, "invalid_payload")
            record(event="message", payload=payload)
            return self.answer(200, "ok")
        record(event="request", method=self.command, path=self.path[:200], source=self.client_address[0],
               bytes=len(raw))
        self.answer(200, "thanks")

    do_GET = do_POST = do_PUT = handle_any


def main():
    port = int(os.environ.get("PORT", "8080"))
    record(event="listening", port=port)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    sys.exit(main())
