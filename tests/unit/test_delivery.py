#!/usr/bin/env python3
"""The live leg's webhook mode (ADR 9), without a tunnel or Cypress Cloud.

    python3 tests/unit/test_delivery.py

tests/e2e/tap.py stands where the tunnel ends: it must hand the receiver
each request as it came, answer with the receiver's answer, decide nothing,
and keep every delivery where only the job can read it. tests/e2e/checks.py
delivery must find Cypress Cloud's delivery for the recorded run among what
the tap kept, pass it only when the receiver started a triage from it, and
write it in the shape tests/e2e/webhook.py replays.
"""
import contextlib
import http.client
import http.server
import importlib.util
import io
import json
import os
import stat
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tap = load("tap", "tests/e2e/tap.py")
checks = load("checks", "tests/e2e/checks.py")

HEADERS = {"Content-Type": "application/json", "X-Cypress-Event": "run.completed", "X-Cypress-Event-Version": "1",
           "X-Cypress-Event-Id": "evt-1", "X-Cypress-Timestamp": "1794500000",
           "X-Cypress-Signature": "sha256=" + "a" * 64}
BODY = b'{"projectId":"abc123","runNumber":7,"status":"failed","runUrl":"https://cloud.cypress.io/projects/abc123/runs/7"}'


class Receiver(http.server.BaseHTTPRequestHandler):
    """Answers like the receiver: 202 and {"triage": "started"} for a signed
    body, 401 otherwise, and remembers what it was sent."""
    got = []

    def log_message(self, *_):
        pass

    def do_GET(self):
        self.reply(200, {"ok": True})

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        Receiver.got.append((self.path, {k.lower(): v for k, v in self.headers.items()}, raw))
        signed = self.headers.get("X-Cypress-Signature")
        self.reply(202 if signed else 401, {"triage": "started"} if signed else {"decision": "unsigned"})

    def reply(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def serve(handler):
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def post(url, raw, headers):
    req = urllib.request.Request(url, data=raw, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        with e:
            return e.code, json.loads(e.read() or b"{}")


class TapTest(unittest.TestCase):
    def setUp(self):
        Receiver.got = []
        self.receiver = serve(Receiver)
        self.dir = tempfile.mkdtemp()
        self.kept = os.path.join(self.dir, "deliveries.jsonl")
        t = tap.Tap("http://127.0.0.1:%d" % self.receiver.server_port, self.kept)
        self.tap = serve(tap.handler_for(t))
        self.url = "http://127.0.0.1:%d" % self.tap.server_port

    def tearDown(self):
        self.tap.shutdown()
        self.receiver.shutdown()

    def quiet_post(self, *a):
        with contextlib.redirect_stdout(io.StringIO()):
            return post(*a)

    def test_the_receiver_gets_the_request_as_it_came(self):
        status, answer = self.quiet_post(self.url + "/webhook", BODY, HEADERS)
        self.assertEqual((status, answer), (202, {"triage": "started"}))
        path, headers, raw = Receiver.got[0]
        self.assertEqual((path, raw), ("/webhook", BODY))
        for k, v in HEADERS.items():
            self.assertEqual(headers[k.lower()], v)

    def test_the_receiver_decides_and_the_tap_passes_its_answer_on(self):
        unsigned = {k: v for k, v in HEADERS.items() if k != "X-Cypress-Signature"}
        self.assertEqual(self.quiet_post(self.url + "/webhook", BODY, unsigned), (401, {"decision": "unsigned"}))

    def test_each_delivery_is_kept_where_only_the_job_reads_it(self):
        self.quiet_post(self.url + "/webhook", BODY, HEADERS)
        self.assertEqual(stat.S_IMODE(os.stat(self.kept).st_mode), 0o600)
        line = json.loads(open(self.kept).read())
        self.assertEqual((line["status"], line["answer"], line["body"]), (202, {"triage": "started"}, BODY.decode()))
        self.assertEqual(line["headers"]["X-Cypress-Signature"], HEADERS["X-Cypress-Signature"])

    def test_a_chunked_body_reaches_the_receiver_whole(self):
        def chunks():
            yield BODY[:40]
            yield BODY[40:]
        req = urllib.request.Request(self.url + "/webhook", data=chunks(), method="POST",
                                     headers=dict(HEADERS, **{"Transfer-Encoding": "chunked"}))
        with contextlib.redirect_stdout(io.StringIO()):
            with urllib.request.urlopen(req, timeout=5) as r:
                self.assertEqual(r.status, 202)
        self.assertEqual(Receiver.got[0][2], BODY)

    def test_a_health_check_is_passed_on_and_not_kept(self):
        with contextlib.redirect_stdout(io.StringIO()):
            with urllib.request.urlopen(self.url + "/healthz", timeout=5) as r:
                self.assertEqual(json.loads(r.read()), {"ok": True})
        self.assertFalse(os.path.exists(self.kept))

    def test_a_body_over_a_mebibyte_is_not_passed_on(self):
        # The tap answers once it has read the length, as the receiver does
        # past 64 KiB, so the body is not sent at all here.
        conn = http.client.HTTPConnection("127.0.0.1", self.tap.server_port, timeout=5)
        conn.putrequest("POST", "/webhook")
        for k, v in dict(HEADERS, **{"Content-Length": str(tap.MAX_BODY + 1)}).items():
            conn.putheader(k, v)
        with contextlib.redirect_stdout(io.StringIO()):
            conn.endheaders()
            self.assertEqual(conn.getresponse().status, 413)
        conn.close()
        self.assertEqual(Receiver.got, [])

    def test_with_no_receiver_the_tap_answers_502_and_starts_nothing(self):
        self.receiver.shutdown()
        self.receiver.server_close()
        status, _ = self.quiet_post(self.url + "/webhook", BODY, HEADERS)
        self.assertEqual(status, 502)


class DeliveryCheckTest(unittest.TestCase):
    def setUp(self):
        self.cwd = os.getcwd()
        self.dir = tempfile.mkdtemp()
        os.chdir(self.dir)
        os.mkdir("out")

    def tearDown(self):
        os.chdir(self.cwd)

    def keep(self, *lines):
        with open("kept.jsonl", "w") as f:
            for line in lines:
                f.write(json.dumps(line) + "\n")

    def verdicts(self, run=7):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            checks.delivery(["kept.jsonl", "abc123", str(run), "0"])
        return [l.split("\t") for l in out.getvalue().splitlines()]

    def line(self, run=7, status=202, answer=None, headers=None):
        body = json.loads(BODY)
        body.update(runNumber=run, runUrl="https://cloud.cypress.io/projects/abc123/runs/%d" % run)
        return {"at": 1, "status": status, "answer": answer or {"triage": "started"},
                "headers": headers or dict(HEADERS), "body": json.dumps(body, separators=(",", ":"))}

    def test_the_run_s_delivery_that_started_a_triage_passes_and_is_written_for_replay(self):
        self.keep(self.line())
        self.assertEqual([v[0] for v in self.verdicts()], ["ok", "ok"])
        d = json.load(open("out/delivery.json"))
        self.assertEqual(set(d), {"headers", "body"})
        self.assertEqual(d["headers"]["X-Cypress-Signature"], HEADERS["X-Cypress-Signature"])
        self.assertEqual(open("out/delivered-runs").read(), "7\n")

    def test_a_delivery_the_receiver_refused_fails(self):
        self.keep(self.line(status=401, answer={"decision": "bad_signature"}))
        self.assertEqual(self.verdicts()[0][0], "bad")
        self.assertEqual(open("out/delivered-runs").read(), "")

    def test_a_delivery_for_another_run_is_not_this_run_s(self):
        self.keep(self.line(run=6))
        v = self.verdicts()
        self.assertEqual((len(v), v[0][0]), (1, "bad"))
        self.assertIn("no delivery from Cypress Cloud for run #7", v[0][1])
        self.assertFalse(os.path.exists("out/delivery.json"))
        self.assertEqual(open("out/delivered-runs").read(), "6\n")

    def test_a_retry_of_an_earlier_run_is_counted_beside_this_one(self):
        self.keep(self.line(run=6), self.line())
        self.assertEqual([v[0] for v in self.verdicts()], ["ok", "ok"])
        self.assertEqual(open("out/delivered-runs").read(), "6\n7\n")

    def test_a_202_that_started_nothing_is_not_a_triage(self):
        self.keep(self.line(answer={"decision": "ignored", "reason": "not a run.completed body"}))
        self.assertEqual(self.verdicts()[0][0], "bad")
        self.assertEqual(open("out/delivered-runs").read(), "")

    def test_header_names_in_lower_case_are_read(self):
        self.keep(self.line(headers={k.lower(): v for k, v in HEADERS.items()}))
        self.assertEqual([v[0] for v in self.verdicts()], ["ok", "ok"])

    def test_a_delivery_with_another_event_header_fails_the_second_check(self):
        self.keep(self.line(headers=dict(HEADERS, **{"X-Cypress-Event": "run.accessibility.completed"})))
        self.assertEqual([v[0] for v in self.verdicts()], ["ok", "bad"])

    def test_nothing_kept_fails(self):
        v = self.verdicts()
        self.assertEqual((len(v), v[0][0]), (1, "bad"))


if __name__ == "__main__":
    unittest.main(verbosity=1)
