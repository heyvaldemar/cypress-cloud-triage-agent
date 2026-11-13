#!/usr/bin/env python3
"""The public URL's end on the runner, in the live leg's webhook mode (ADR 9).

    tests/e2e/tap.py LISTEN_PORT RECEIVER_PORT DELIVERIES_FILE

cloudflared hands every request that reaches the tunnel's hostname to
127.0.0.1:LISTEN_PORT. This sends it on to the receiver as it came, through
the port-forward on 127.0.0.1:RECEIVER_PORT, and answers with the receiver's
status and body. Nothing is decided here: the receiver decides, and a
request it would refuse is refused.

Every POST to /webhook is kept in DELIVERIES_FILE, one JSON line each: when
it came, its Content-Type and X-Cypress-* headers as they arrived, its body,
and the receiver's status and answer. tests/e2e/checks.py delivery reads
it to find Cypress Cloud's delivery for the recorded run, and run.sh replays
that delivery at the public URL. A real delivery names the project and the
commit, so the file is the job's own (mode 600, outside out/) and is never
uploaded. One line per request goes to stdout: the method, the path, the
receiver's status and decision, and the event ID as claimed. No body, no
signature.

Standard library only.
"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY = 1024 * 1024
# Hop-by-hop headers, and the two urllib sets itself, are not passed on.
DROP = {"host", "connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade",
        "proxy-connection", "content-length"}
KEEP = ("content-type", "x-cypress-")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_):
        return None


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)


def read_body(rfile, headers):
    """The request's body, by Content-Length or chunked; None past MAX_BODY."""
    if "chunked" in (headers.get("Transfer-Encoding") or "").lower():
        out = b""
        while True:
            size = int(rfile.readline().split(b";")[0].strip() or b"0", 16)
            if size == 0:
                while rfile.readline() not in (b"\r\n", b"\n", b""):
                    pass
                return out
            if len(out) + size > MAX_BODY:
                return None
            out += rfile.read(size)
            rfile.readline()
    length = int(headers.get("Content-Length") or 0)
    if length > MAX_BODY:
        return None
    return rfile.read(length) if length else b""


def forward(target, method, path, headers, body):
    """The receiver's (status, content type, body) for the request."""
    sent = {k: v for k, v in headers.items() if k.lower() not in DROP}
    req = urllib.request.Request(target + path, data=body if method == "POST" else None,
                                 method=method, headers=sent)
    try:
        with OPENER.open(req, timeout=15) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read(64 * 1024)
    except urllib.error.HTTPError as e:
        with e:
            return e.code, e.headers.get("Content-Type", ""), e.read(64 * 1024)
    except (urllib.error.URLError, OSError) as e:
        return 502, "application/json", json.dumps({"tap": "the receiver did not answer: %s" % e}).encode()


def answer_of(raw):
    """The receiver's answer, a small JSON object: {"triage": "started"} when
    it started one, {"decision": ..., "reason": ...} otherwise."""
    try:
        answer = json.loads(raw or b"{}")
    except ValueError:
        return {}
    return answer if isinstance(answer, dict) else {}


class Tap:
    def __init__(self, target, deliveries):
        self.target, self.deliveries, self.lock = target, deliveries, threading.Lock()

    def keep(self, headers, body, status, answer):
        line = {"at": int(time.time()), "status": status, "answer": answer,
                "headers": {k: v for k, v in headers.items() if k.lower().startswith(KEEP)}}
        try:
            line["body"] = body.decode("utf-8")
        except UnicodeDecodeError:
            line["body"], line["not_utf8"] = body.decode("latin-1"), True
        with self.lock:
            fd = os.open(self.deliveries, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as f:
                f.write(json.dumps(line, sort_keys=True) + "\n")


def handler_for(tap):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            pass

        def answer(self, status, ctype, raw):
            self.send_response(status)
            self.send_header("Content-Type", ctype or "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(raw)
            self.close_connection = True

        def relay(self, method):
            body = read_body(self.rfile, self.headers) if method == "POST" else b""
            if body is None:
                self.answer(413, "application/json", b'{"tap": "a body over 1 MiB is not passed on"}')
                status, answer = 413, {}
            else:
                status, ctype, raw = forward(tap.target, method, self.path, self.headers, body)
                answer = answer_of(raw)
                if method == "POST" and self.path == "/webhook":
                    tap.keep(self.headers, body, status, answer)
                self.answer(status, ctype, raw)
            print(json.dumps({"method": method, "path": self.path, "status": status,
                              "decision": answer.get("decision") or answer.get("triage", ""),
                              "event_id": self.headers.get("X-Cypress-Event-Id", "")}), flush=True)

        def do_GET(self):
            self.relay("GET")

        def do_POST(self):
            self.relay("POST")

    return Handler


def main():
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    listen, receiver, deliveries = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
    tap = Tap("http://127.0.0.1:%d" % receiver, deliveries)
    httpd = ThreadingHTTPServer(("127.0.0.1", listen), handler_for(tap))
    print(json.dumps({"listening": listen, "receiver": receiver}), flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
