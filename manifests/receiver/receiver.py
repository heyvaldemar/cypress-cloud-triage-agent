"""The door a finished Cypress run knocks on: a run.completed webhook receiver.

Cypress Cloud sends run.completed to a public URL. Until a project has a
plan with webhooks, the end of the CI job sends the same body, signed the
same way (scripts/send-run.py). Either way this server is the only thing
that can start a triage, and it decides in this order, the first failure
answering the request:

  1. signed: X-Cypress-Signature is "sha256=" and the hex HMAC-SHA256 of
     "<X-Cypress-Timestamp>.<raw body>" under the signing secret, compared
     in constant time. Nothing unsigned is read further;
  2. fresh: the timestamp is a Unix time in seconds within five minutes of
     now, either way. A value that is not digits is refused, not coerced:
     docs.cypress.io's Node example computes Number(timestamp), and NaN
     fails no "older than five minutes" test (the timestamp is signed, so
     only a secret holder could send one, but a check should not pass what
     it cannot read);
  3. not replayed: a signature already seen is refused. The signature binds
     the timestamp and the body, so an exact replay inside the window is
     caught here. X-Cypress-Event-Id is not covered by the signature, so it
     is never the replay key: a captured delivery replayed with a new
     Event-Id is the same signature;
  4. a run.completed body: X-Cypress-Event is not signed either, so the
     body decides which event this is. The project ID must be one this door
     serves, the run number a positive integer, the status one Cypress
     sends, and the run URL the one those two make;
  5. once per run: a failed, errored or timed-out run starts one triage,
     keyed on the signed project ID and run number. A retry (new timestamp,
     same body) is answered 200 and starts nothing. The Job's name is the
     key too, so the API server refuses a second one after a restart.

A triage is a Job made from a fixed template (job.json) in the
triage-agents namespace. It carries the project ID and the run number and
nothing else from the payload: the commit message, the branch and the
author are text anyone with a pull request writes, and the agent reads what
it needs through the Cloud MCP door. Admission holds the Job to the
template's shape whatever this server sends (manifests/admission.yaml).

Every decision is one JSON line on stdout. Standard library only, on a
pinned python:3.13-alpine.
"""
import hashlib
import hmac
import json
import os
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SECRET_FILE = os.environ.get("SIGNING_SECRET_FILE", "/var/run/secrets/webhook/secret")
JOB_TEMPLATE = os.environ.get("JOB_TEMPLATE", "/etc/receiver/job.json")
NAMESPACE = os.environ.get("JOB_NAMESPACE", "triage-agents")
PROJECTS = [p for p in os.environ.get("PROJECTS", "").split(",") if p]
API = "https://kubernetes.default.svc"
SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
WINDOW = 300
MAX_BODY = 64 * 1024
STATUSES = {"passed", "failed", "errored", "timedOut", "cancelled"}
TRIAGE = {"failed", "errored", "timedOut"}
PROJECT_ID = re.compile(r"[a-z0-9]{6}")
SIGNATURE = re.compile(r"sha256=[0-9a-f]{64}")


class Verdict:
    def __init__(self, status, decision, reason, start=None, run=None):
        self.status, self.decision, self.reason, self.start, self.run = status, decision, reason, start, run


def header(headers, name):
    """A header by name, any case. http.server's headers already ignore
    case; a plain dict, as the tests pass, does not."""
    if hasattr(headers, "get_all"):
        return headers.get(name)
    for k, v in headers.items():
        if k.lower() == name.lower():
            return v
    return None


def sign(secret, timestamp, raw):
    return "sha256=" + hmac.new(secret, timestamp.encode() + b"." + raw, hashlib.sha256).hexdigest()


class Door:
    def __init__(self, secret, projects, window=WINDOW, clock=time.time):
        # Fail closed: a door with no secret, or a guessable one, would take
        # anyone's word. Cypress Cloud asks for 16 characters at least.
        if len(secret) < 16:
            raise ValueError("the signing secret is shorter than 16 bytes")
        if not projects or not all(PROJECT_ID.fullmatch(p) for p in projects):
            raise ValueError("PROJECTS must name at least one six-character Cypress project ID")
        self.secret, self.projects, self.window, self.clock = secret, set(projects), window, clock
        self.seen = {}
        self.started = set()
        self.lock = threading.Lock()

    def check(self, headers, raw):
        signature = header(headers, "X-Cypress-Signature")
        timestamp = header(headers, "X-Cypress-Timestamp")
        if not signature or not timestamp:
            return Verdict(401, "unsigned", "X-Cypress-Signature and X-Cypress-Timestamp are required")
        if not SIGNATURE.fullmatch(signature):
            return Verdict(401, "bad_signature", "the signature is not sha256= and 64 hex digits")
        if not hmac.compare_digest(signature, sign(self.secret, timestamp, raw)):
            return Verdict(401, "bad_signature", "the signature does not match the timestamp and body")
        if not re.fullmatch(r"[0-9]{1,12}", timestamp):
            return Verdict(400, "bad_timestamp", "the timestamp is not a Unix time in seconds")
        now = self.clock()
        if abs(now - int(timestamp)) > self.window:
            return Verdict(400, "stale", "the timestamp is more than %d seconds from now" % self.window)
        with self.lock:
            for s, until in list(self.seen.items()):
                if until < now:
                    del self.seen[s]
            if signature in self.seen:
                return Verdict(409, "replayed", "this signature was already delivered")
            self.seen[signature] = int(timestamp) + 2 * self.window
        try:
            body = json.loads(raw)
        except ValueError:
            return Verdict(400, "bad_body", "the body is not JSON")
        if not isinstance(body, dict):
            return Verdict(400, "bad_body", "the body is not a JSON object")
        if "accessibility" in body or "uiCoverage" in body:
            return Verdict(202, "ignored", "not a run.completed body")
        project, number, status = body.get("projectId"), body.get("runNumber"), body.get("status")
        if not isinstance(project, str) or not PROJECT_ID.fullmatch(project):
            return Verdict(400, "bad_body", "projectId is not a Cypress project ID")
        if type(number) is not int or not 0 < number < 10 ** 9:
            return Verdict(400, "bad_body", "runNumber is not a positive integer")
        if status not in STATUSES:
            return Verdict(400, "bad_body", "status is not one Cypress sends")
        run = (project, number)
        if project not in self.projects:
            return Verdict(403, "unknown_project", "this door does not triage that project", run=run)
        if body.get("runUrl") != "https://cloud.cypress.io/projects/%s/runs/%d" % run:
            return Verdict(400, "bad_body", "runUrl is not the run the projectId and runNumber name", run=run)
        if status not in TRIAGE:
            return Verdict(200, "nothing_to_triage", "the run %s" % status, run=run)
        with self.lock:
            if run in self.started:
                return Verdict(200, "duplicate", "a triage of this run was already started", run=run)
            self.started.add(run)
        return Verdict(202, "start", "triage started", start=run, run=run)

    def forget(self, run):
        """A start the API server refused for a reason other than a duplicate:
        let Cypress's retry try again."""
        with self.lock:
            self.started.discard(run)


def job_for(template, run):
    project, number = run
    job = json.loads(json.dumps(template))
    job["metadata"]["name"] = "triage-%s-%d" % (project, number)
    job["metadata"].setdefault("labels", {}).update({"triage.heyvaldemar.com/project": project})
    container = job["spec"]["template"]["spec"]["containers"][0]
    env = [e for e in container.get("env", []) if e["name"] not in ("RUN_PROJECT", "RUN_NUMBER")]
    container["env"] = env + [{"name": "RUN_PROJECT", "value": project}, {"name": "RUN_NUMBER", "value": str(number)}]
    return job


def create_job(job):
    """HTTP status of the create: 201, 409 for a Job of that name, or other."""
    with open(os.path.join(SA_DIR, "token"), encoding="utf-8") as f:
        token = f.read().strip()
    req = urllib.request.Request(
        "%s/apis/batch/v1/namespaces/%s/jobs" % (API, NAMESPACE), data=json.dumps(job).encode(), method="POST",
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
    ctx = ssl.create_default_context(cafile=os.path.join(SA_DIR, "ca.crt"))
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
            return r.status, ""
    except urllib.error.HTTPError as e:
        with e:
            try:
                message = json.load(e).get("message", "")
            except ValueError:
                message = ""
        return e.code, message[:300]


DOOR = None
TEMPLATE = None


def audit(**line):
    line["at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(json.dumps(line, sort_keys=True), flush=True)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "triage-receiver"
    sys_version = ""

    def log_message(self, *_):
        pass  # the audit line is the log

    def send(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/healthz":
            return self.send(200, {"ok": True})
        self.send(405, {"error": "POST only"})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            self.close_connection = True
            audit(decision="too_large", status=413)
            return self.send(413, {"error": "the body is larger than %d bytes" % MAX_BODY})
        raw = self.rfile.read(length) if length else b""
        if self.path != "/webhook":
            return self.send(404, {"error": "not found"})
        v = DOOR.check(self.headers, raw)
        # The event ID and request ID are logged as claimed: neither is signed.
        claimed = {"event_id": self.headers.get("X-Cypress-Event-Id", ""),
                   "request_id": self.headers.get("X-Cypress-Request-Id", "")}
        run = {"project": v.run[0], "run": v.run[1]} if v.run else {}
        if v.start:
            status, message = create_job(job_for(TEMPLATE, v.start))
            if status == 201:
                audit(decision="started", status=202, job="triage-%s-%d" % v.start, **run, **claimed)
                return self.send(202, {"triage": "started"})
            if status == 409:
                audit(decision="duplicate", status=200, reason="the Job already exists", **run, **claimed)
                return self.send(200, {"triage": "already started"})
            DOOR.forget(v.start)
            audit(decision="start_failed", status=503, reason="API server answered %d: %s" % (status, message),
                  **run, **claimed)
            return self.send(503, {"error": "the triage could not be started"})
        audit(decision=v.decision, status=v.status, reason=v.reason, **run, **claimed)
        self.send(v.status, {"decision": v.decision, "reason": v.reason})


def main():
    global DOOR, TEMPLATE
    with open(SECRET_FILE, "rb") as f:
        secret = f.read().strip()
    DOOR = Door(secret, PROJECTS)
    with open(JOB_TEMPLATE, encoding="utf-8") as f:
        TEMPLATE = json.load(f)
    port = int(os.environ.get("PORT", "8080"))
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    audit(event="listening", port=port, projects=sorted(DOOR.projects), namespace=NAMESPACE)
    httpd.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
