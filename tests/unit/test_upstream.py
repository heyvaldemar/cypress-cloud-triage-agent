#!/usr/bin/env python3
"""The door's client for Cloud MCP, against servers on this machine.

    python3 tests/unit/test_upstream.py

The client must speak both ways an MCP server may answer: 2026-07-28, as the
door's own stand-in does, and the initialize handshake with a session and an
event stream. What it must never do: send the token anywhere but its one
URL, follow a redirect, or carry an error answer's body into an exception.
"""
import importlib.util
import json
import os
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "manifests", "cloud-mcp"))
import upstream  # noqa: E402

spec = importlib.util.spec_from_file_location("door", os.path.join(ROOT, "manifests/cloud-mcp/server.py"))
door = importlib.util.module_from_spec(spec)
spec.loader.exec_module(door)

TOKEN = "cmcp-unit-test-token"


def serve(handler):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, "http://127.0.0.1:%d/mcp" % httpd.server_address[1]


def stop(httpd):
    httpd.shutdown()
    httpd.server_close()


class Recorder(BaseHTTPRequestHandler):
    """An MCP server from before 2026-07-28: a handshake, a session on the
    backend a cookie names, answers as an event stream that stays open, tools
    listed over two pages."""
    protocol_version = "HTTP/1.1"
    seen = []
    sessions = set()
    forget_once = False

    def log_message(self, *_):
        pass

    def reply(self, status, body=b"", ctype="application/json", extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        msg = json.loads(raw)
        cls = type(self)
        cls.seen.append({"method": msg.get("method"), "auth": self.headers.get("Authorization"),
                         "session": self.headers.get("Mcp-Session-Id"),
                         "version": self.headers.get("MCP-Protocol-Version")})
        method = msg.get("method")
        if method == "server/discover":
            return self.reply(400, b'{"error": "Bad Request: No valid session ID provided"}')
        if method == "initialize":
            sid = "s%d" % len(cls.seen)
            cls.sessions.add(sid)
            body = json.dumps({"jsonrpc": "2.0", "id": msg["id"],
                               "result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                                          "serverInfo": {"name": "recorder", "version": "0"}}}).encode()
            return self.reply(200, body, extra={"Mcp-Session-Id": sid, "Set-Cookie": "BACKEND=b1; Path=/"})
        if "BACKEND=b1" not in (self.headers.get("Cookie") or ""):
            # A load balancer that pins a client to the backend holding its
            # session, by a cookie: without it the session is not found.
            return self.reply(400, b'{"error": "no session on this backend"}')
        if self.headers.get("Mcp-Session-Id") not in cls.sessions:
            return self.reply(400, b'{"error": "no session"}')
        if cls.forget_once and method == "tools/call":
            cls.forget_once = False
            cls.sessions.clear()
            return self.reply(404, b'{"error": "session expired"}')
        if "id" not in msg:
            return self.reply(202)
        if method == "tools/list":
            cursor = (msg.get("params") or {}).get("cursor")
            result = {"tools": [{"name": "cypress_get_runs"}], "nextCursor": "p2"} if not cursor else \
                {"tools": [{"name": "cypress_get_failed_tests"}]}
        else:
            result = {"content": [{"type": "text", "text": json.dumps(msg["params"])}]}
        # A progress notification first, then the answer, then the stream
        # stays open: the client must stop at the answer.
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(b'event: message\ndata: {"jsonrpc": "2.0", "method": "notifications/progress"}\n\n')
        self.wfile.write(("event: message\ndata: %s\n\n" % json.dumps(
            {"jsonrpc": "2.0", "id": msg["id"], "result": result})).encode())
        self.wfile.flush()
        time.sleep(3)


class Redirect(BaseHTTPRequestHandler):
    """Sends every request elsewhere with a 302, which urllib would follow
    as a GET with the Authorization header still on it."""
    target = ""

    def log_message(self, *_):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(302)
        self.send_header("Location", type(self).target)
        self.send_header("Content-Length", "0")
        self.end_headers()


class Elsewhere(BaseHTTPRequestHandler):
    """Where a redirect points: it writes down any request that reaches it."""
    reached = []

    def log_message(self, *_):
        pass

    def do_GET(self):
        type(self).reached.append(self.headers.get("Authorization"))
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_POST = do_GET


class Echo(BaseHTTPRequestHandler):
    """A server that answers with an error and quotes the request's token."""
    status = 500

    def log_message(self, *_):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        body = ("rejected: %s" % self.headers.get("Authorization")).encode()
        self.send_response(type(self).status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class StandIn(door.Handler):
    """The door's own stand-in, every caller taken as the triage agent of
    run 7: the client speaks 2026-07-28 to it."""


class UpstreamTest(unittest.TestCase):
    def setUp(self):
        Recorder.seen, Recorder.sessions, Recorder.forget_once = [], set(), False

    def client(self, url, **kw):
        return upstream.Upstream(url, TOKEN, timeout=2, require_tls=False, **kw)

    def test_speaks_2026_07_28_to_the_door_stand_in(self):
        import tempfile
        runs = tempfile.mkdtemp()
        with open(os.path.join(runs, "kind01-7.json"), "w") as f:
            json.dump({"summary": {"runNumber": 7}, "tests": []}, f)
        door.RUNS_DIR = runs
        door.SERVER = door.Server(door.load_policy(os.path.join(ROOT, "manifests/cloud-mcp/policy.json")))
        door.audit = lambda **line: None
        door.identify = lambda token, audience: ("system:serviceaccount:triage-agents:triage-agent",
                                                 "triage-kind01-7-x2b9q") if token == TOKEN else None
        httpd, url = serve(StandIn)
        try:
            c = self.client(url)
            self.assertEqual(c.connect(), "2026-07-28")
            self.assertIsNone(c.session)
            self.assertEqual(sorted(t["name"] for t in c.list_tools()),
                             ["cypress_get_failed_tests", "cypress_get_runs"])
            r = c.call_tool("cypress_get_runs", {"projectId": "kind01", "runNumber": 7})
            self.assertEqual(json.loads(r["content"][0]["text"]), {"runNumber": 7})
            with self.assertRaises(upstream.RPCError):
                c.call_tool("cypress_get_test_screenshots", {"projectId": "kind01", "runNumber": 7})
        finally:
            stop(httpd)

    def test_falls_back_to_the_handshake_and_keeps_the_session(self):
        httpd, url = serve(Recorder)
        try:
            c = self.client(url)
            self.assertEqual(c.connect(), "2025-06-18")
            names = [t["name"] for t in c.list_tools()]
            started = time.monotonic()
            r = c.call_tool("cypress_get_runs", {"runUrl": "u"})
            self.assertLess(time.monotonic() - started, 2, "the client waited for the stream to close")
        finally:
            stop(httpd)
        self.assertEqual(names, ["cypress_get_runs", "cypress_get_failed_tests"])
        self.assertEqual(json.loads(r["content"][0]["text"]), {"name": "cypress_get_runs", "arguments": {"runUrl": "u"}})
        methods = [s["method"] for s in Recorder.seen]
        self.assertEqual(methods, ["server/discover", "initialize", "notifications/initialized",
                                   "tools/list", "tools/list", "tools/call"])
        self.assertTrue(all(s["auth"] == "Bearer " + TOKEN for s in Recorder.seen))
        self.assertTrue(all(s["session"] for s in Recorder.seen[2:]))
        self.assertTrue(all(s["version"] == "2025-06-18" for s in Recorder.seen[2:]))

    def test_a_forgotten_session_is_opened_again_once(self):
        httpd, url = serve(Recorder)
        try:
            c = self.client(url)
            Recorder.forget_once = True
            r = c.call_tool("cypress_get_runs", {})
        finally:
            stop(httpd)
        self.assertIn("content", r)
        self.assertEqual([s["method"] for s in Recorder.seen].count("initialize"), 2)

    def test_a_redirect_is_not_followed(self):
        Elsewhere.reached = []
        target, target_url = serve(Elsewhere)
        Redirect.target = target_url
        httpd, url = serve(Redirect)
        try:
            with self.assertRaises(upstream.UpstreamError) as e:
                self.client(url).call_tool("cypress_get_runs", {})
        finally:
            stop(httpd)
            stop(target)
        self.assertEqual(Elsewhere.reached, [], "the token reached the redirect's target")
        self.assertEqual(getattr(e.exception, "status", None), 302)

    def test_a_refused_token_stops_at_once(self):
        Echo.status = 401
        httpd, url = serve(Echo)
        try:
            with self.assertRaises(upstream.HTTPStatus) as e:
                self.client(url).connect()
        finally:
            stop(httpd)
        self.assertEqual(e.exception.status, 401)

    def test_an_error_answer_never_reaches_the_message(self):
        Echo.status = 500
        httpd, url = serve(Echo)
        try:
            with self.assertRaises(upstream.UpstreamError) as e:
                self.client(url).call_tool("cypress_get_runs", {})
        finally:
            stop(httpd)
        self.assertNotIn(TOKEN, str(e.exception))
        self.assertNotIn("rejected", str(e.exception))

    def test_the_token_is_not_in_its_repr(self):
        c = upstream.Upstream("https://mcp.example.test/mcp", TOKEN)
        self.assertNotIn(TOKEN, repr(c))
        self.assertNotIn(TOKEN, repr(vars(c).get("url")))

    def test_https_only_and_one_line_tokens(self):
        with self.assertRaises(ValueError):
            upstream.Upstream("http://mcp.example.test/mcp", TOKEN)
        with self.assertRaises(ValueError):
            upstream.Upstream("https://mcp.example.test/mcp", TOKEN + "\r\nX-Other: 1")


if __name__ == "__main__":
    unittest.main(verbosity=1)
