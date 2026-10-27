"""The triage agent's one way out: it posts one Slack message per run, and
writes the part of it the agent cannot.

A triage agent reads text that strangers wrote, so what it writes is not
trusted either. It can be told to hide a failure, to ping the whole
channel, or to put a link in its summary whose address carries the data.
In Slack a link does not need a click to leak: Slack's servers fetch it to
show a preview. So this server, not the agent, decides what reaches Slack:

  1. who is posting: a token bound to this server's audience, checked by
     the API server's TokenReview, from the triage agent's ServiceAccount
     and nothing else;
  2. which run: the one the agent's pod was started for (the receiver
     names the Job triage-<project>-<run>), read from the token, not from
     the message, so an agent cannot post about another run;
  3. once: one message per run. A second is refused;
  4. the facts: the first line is written here, from Cypress Cloud (this
     server's own read, through the Cloud MCP door): the run's link, its
     status and how many tests failed. An agent told to leave a failure out
     cannot change that count;
  5. the agent's words, defused: shown as a quote under a line that says
     they are the agent's, with Slack's control characters escaped (so no
     <!channel>, no <url|label> link) and every address outside Cypress's
     own hosts broken (hxxp://host[.]example), and sent with link and media
     previews off.

The Slack webhook URL is a credential: it is mounted here and nowhere else.
Every decision is one JSON line on stdout. Standard library only, on a
pinned python:3.13-alpine.
"""
import hashlib
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

AUDIENCE = os.environ.get("NOTIFY_AUDIENCE", "notify.triage.heyvaldemar.com")
AGENT = os.environ.get("AGENT_IDENTITY", "system:serviceaccount:triage-agents:triage-agent")
SLACK_URL_FILE = os.environ.get("SLACK_URL_FILE", "/var/run/secrets/slack/url")
CLOUD_MCP_URL = os.environ.get("CLOUD_MCP_URL", "http://cloud-mcp.cloud-mcp.svc.cluster.local:8080/mcp")
CLOUD_MCP_TOKEN = os.environ.get("CLOUD_MCP_TOKEN_FILE", "/var/run/secrets/cloud-mcp/token")
API = "https://kubernetes.default.svc"
SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
PROTOCOL = "2026-07-28"
MAX_BODY = 16 * 1024
MAX_TEXT = 3000
TIMEOUT = 5
TRIAGE_POD = re.compile(r"triage-([a-z0-9]{6})-([1-9][0-9]{0,8})-[a-z0-9]{5}")
# Addresses a summary may keep as links: Cypress's own. Anything else is
# shown, broken, as text.
ALLOWED_HOSTS = {"cloud.cypress.io", "docs.cypress.io", "on.cypress.io"}
URL = re.compile(r"(?i)\b(?:https?|ftp)://[^\s<>\"'|`]+")


def defang_url(m):
    url = m.group(0)
    # The host is after any user@ and before any :port.
    host = re.sub(r"(?i)^[a-z]+://", "", url).split("/")[0].split("@")[-1].split(":")[0].lower()
    if host in ALLOWED_HOSTS and url.lower().startswith("https://"):
        return url
    scheme, _, rest = url.partition("://")
    hostport, slash, path = rest.partition("/")
    return scheme.lower().replace("t", "x") + "://" + hostport.replace(".", "[.]") + slash + path


def escape(text):
    """Slack's own escaping: with &, < and > escaped, nothing in the text is
    read as a link, a mention or a channel."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def defuse(text):
    text = text[:MAX_TEXT] + (" [truncated]" if len(text) > MAX_TEXT else "")
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)
    return escape(URL.sub(defang_url, text))


def message(run, facts, agent_text):
    project, number = run
    url = "https://cloud.cypress.io/projects/%s/runs/%d" % (project, number)
    head = "*Cypress run <%s|%s #%d> %s:* %s of %s tests failed." % (
        url, project, number, escape(str(facts.get("status", "unknown"))),
        facts.get("totalFailed", "?"), facts.get("totalTests", "?"))
    quoted = "\n".join(">" + line for line in defuse(agent_text).splitlines()) or ">(empty)"
    return {"text": head + "\n_The counts are Cypress Cloud's. The triage agent's reading, not checked:_\n" + quoted,
            "unfurl_links": False, "unfurl_media": False}


# --- who is calling ----------------------------------------------------------

_reviews = {}
_reviews_lock = threading.Lock()


def identify(token):
    """(ServiceAccount, pod) for a token the API server accepts for this
    server's audience, else None. Cached for 30 seconds by the token's hash."""
    digest = hashlib.sha256(token.encode()).hexdigest()
    now = time.monotonic()
    with _reviews_lock:
        hit = _reviews.get(digest)
        if hit and hit[0] > now:
            return hit[1]
    with open(os.path.join(SA_DIR, "token"), encoding="utf-8") as f:
        own = f.read().strip()
    body = json.dumps({"apiVersion": "authentication.k8s.io/v1", "kind": "TokenReview",
                       "spec": {"token": token, "audiences": [AUDIENCE]}}).encode()
    req = urllib.request.Request(API + "/apis/authentication.k8s.io/v1/tokenreviews", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + own})
    ctx = ssl.create_default_context(cafile=os.path.join(SA_DIR, "ca.crt"))
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
        status = json.load(r).get("status", {})
    who = None
    if status.get("authenticated") and AUDIENCE in status.get("audiences", []):
        user = status.get("user", {})
        pod = (user.get("extra") or {}).get("authentication.kubernetes.io/pod-name", [""])[0]
        who = (user.get("username", ""), pod)
    with _reviews_lock:
        _reviews[digest] = (now + 30, who)
    return who


def run_facts(run):
    """cypress_get_runs for the run, through the Cloud MCP door, as this
    server's own identity."""
    project, number = run
    params = {"name": "cypress_get_runs", "arguments": {"projectId": project, "runNumber": number},
              "_meta": {"io.modelcontextprotocol/protocolVersion": PROTOCOL,
                        "io.modelcontextprotocol/clientInfo": {"name": "notifier", "version": "1.0.0"},
                        "io.modelcontextprotocol/clientCapabilities": {}}}
    with open(CLOUD_MCP_TOKEN, encoding="utf-8") as f:
        token = f.read().strip()
    req = urllib.request.Request(
        CLOUD_MCP_URL, method="POST",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params}).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                 "MCP-Protocol-Version": PROTOCOL, "Mcp-Method": "tools/call", "Mcp-Name": "cypress_get_runs",
                 "Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        result = json.load(r)["result"]
    if result.get("isError"):
        raise ValueError(" ".join(c.get("text", "") for c in result.get("content", [])))
    return json.loads(result["content"][0]["text"])


def post_to_slack(payload):
    with open(SLACK_URL_FILE, encoding="utf-8") as f:
        url = f.read().strip()
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.status


posted = set()
posted_lock = threading.Lock()


def audit(**line):
    line["at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(json.dumps(line, sort_keys=True), flush=True)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "triage-notifier"
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

    def refuse(self, status, decision, reason, **extra):
        audit(decision=decision, status=status, reason=reason, **extra)
        self.send(status, {"decision": decision, "reason": reason})

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
            return self.refuse(413, "too_large", "the body is larger than %d bytes" % MAX_BODY)
        raw = self.rfile.read(length) if length else b""
        if self.path != "/notify":
            return self.send(404, {"error": "not found"})
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return self.refuse(401, "unauthenticated", "no bearer token")
        try:
            who = identify(auth[len("Bearer "):].strip())
        except (urllib.error.URLError, OSError, ValueError) as e:
            return self.refuse(503, "unavailable", "identity check unavailable: %s" % type(e).__name__)
        if who is None:
            return self.refuse(401, "unauthenticated", "token not accepted for this audience")
        identity, pod = who
        if identity != AGENT:
            return self.refuse(403, "denied", "only the triage agent posts here", identity=identity, pod=pod)
        m = TRIAGE_POD.fullmatch(pod or "")
        if not m:
            return self.refuse(403, "denied", "the token is not bound to a triage pod", identity=identity, pod=pod)
        run = (m.group(1), int(m.group(2)))
        where = {"identity": identity, "pod": pod, "project": run[0], "run": run[1]}
        try:
            text = json.loads(raw).get("text")
        except (ValueError, AttributeError):
            text = None
        if not isinstance(text, str):
            return self.refuse(400, "bad_body", "the body is a JSON object with a text string", **where)
        with posted_lock:
            if run in posted:
                return self.refuse(409, "second_message", "one message per run", **where)
            posted.add(run)
        try:
            facts = run_facts(run)
            payload = message(run, facts, text)
            status = post_to_slack(payload)
        except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
            with posted_lock:
                posted.discard(run)
            return self.refuse(502, "not_delivered", "%s: %s" % (type(e).__name__, str(e)[:200]), **where)
        audit(decision="posted", status=202, slack=status, failed=facts.get("totalFailed"),
              agent_chars=len(text), **where)
        self.send(202, {"decision": "posted"})


def main():
    port = int(os.environ.get("PORT", "8080"))
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    audit(event="listening", port=port, audience=AUDIENCE, agent=AGENT, allowed_hosts=sorted(ALLOWED_HOSTS))
    httpd.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
