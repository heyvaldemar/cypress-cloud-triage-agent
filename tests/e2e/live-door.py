"""Run inside the live door's pod by tests/e2e/run.sh, step 9 (ADR 8):

    kubectl -n cloud-mcp exec -i <door pod> -c server -- python3 -I - < tests/e2e/live-door.py

What the door itself can reach, and that the token is what Cloud MCP lets
in. One verdict per line, "ok" or "bad", a tab, and what was checked. Each
refusal sits next to its control: the door reaches Cloud MCP on 443, and
cannot reach the internet by address, another host by name, or Cloud MCP's
own name on another port. Cloud MCP refuses a token that is not the door's,
while the door's own reads in the same run were answered.
"""
import socket
import sys

sys.path.insert(0, "/app")
import upstream  # noqa: E402  the door's own client, from its ConfigMap


def connect(host, port):
    try:
        socket.create_connection((host, port), timeout=5).close()
        return "reached"
    except OSError as e:
        return "blocked (%s)" % type(e).__name__


got = connect("mcp.cypress.io", 443)
print("%s\tthe door reaches Cloud MCP on 443: %s" % ("ok" if got == "reached" else "bad", got))
for what, host, port in [("the internet by address, 1.1.1.1 on 443", "1.1.1.1", 443),
                         ("GitHub's API by name, on 443", "api.github.com", 443),
                         ("Cloud MCP's own name on port 80", "mcp.cypress.io", 80)]:
    got = connect(host, port)
    print("%s\tthe door cannot reach %s: %s" % ("ok" if got != "reached" else "bad", what, got))

try:
    upstream.Upstream("https://mcp.cypress.io/mcp", "not-the-doors-token").connect()
    got = "accepted"
except upstream.HTTPStatus as e:
    got = "HTTP %d" % e.status
except upstream.UpstreamError as e:
    got = str(e)
print("%s\tCloud MCP refuses a token that is not the door's: %s" % ("ok" if got == "HTTP 401" else "bad", got))
