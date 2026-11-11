"""The door's client for Cypress Cloud MCP (https://mcp.cypress.io/mcp).

An MCP client over streamable HTTP, standard library only. The door is the
one thing that holds the Cypress token, and this is the one thing that sends
it: to the one URL it was built for, in the Authorization header, over TLS
with the system's roots. It follows no redirect, uses no proxy from the
environment, and never puts the token, a header or the body of an error
answer into an exception, a log or its repr. It keeps the cookies that one
URL sets: mcp.cypress.io sits behind a load balancer that pins a client to
one backend with a cookie, and a session lives on one backend.

Cloud MCP's protocol version is not published. The client first asks the
way 2026-07-28 does (server/discover, no session) and falls back to the
initialize handshake of the earlier versions, keeping the session it is
given and opening a new one once if the server forgets it.
"""
import http.cookiejar
import json
import ssl
import threading
import urllib.error
import urllib.request

STATELESS = "2026-07-28"
HANDSHAKE = ("2025-11-25", "2025-06-18", "2025-03-26")
CLIENT_INFO = {"name": "cypress-cloud-triage-door", "version": "1.0.0"}
MAX_RESPONSE = 1024 * 1024
MAX_PAGES = 10


class UpstreamError(Exception):
    """A request that got no result. The message names what failed, never a
    token, a header or a body the server sent with an error status."""


class HTTPStatus(UpstreamError):
    def __init__(self, status):
        super().__init__("upstream answered HTTP %d" % status)
        self.status = status


class RPCError(UpstreamError):
    def __init__(self, code, message):
        super().__init__("upstream error %s: %s" % (code, str(message)[:200]))
        self.code = code


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # A redirect would carry the Authorization header to wherever it points.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Upstream:
    def __init__(self, url, token, timeout=15, context=None, require_tls=True):
        if require_tls and not url.startswith("https://"):
            raise ValueError("the upstream is reached over https only")
        if not token or any(c in token for c in "\r\n"):
            raise ValueError("the token is empty or spans lines")
        self.url, self.timeout = url, timeout
        self._token = token
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect(),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
            urllib.request.HTTPSHandler(context=context or ssl.create_default_context()))
        self.version = None
        self.session = None
        self._id = 0
        self._lock = threading.RLock()

    def __repr__(self):
        return "Upstream(%r, version=%r)" % (self.url, self.version)

    # --- one exchange --------------------------------------------------------

    def _headers(self, method, name):
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
             "Authorization": "Bearer " + self._token}
        if self.version:
            h["MCP-Protocol-Version"] = self.version
        if self.version == STATELESS:
            h["Mcp-Method"] = method
            if name is not None:
                h["Mcp-Name"] = name
        if self.session:
            h["Mcp-Session-Id"] = self.session
        return h

    def _meta(self, params):
        if self.version != STATELESS:
            return params
        out = dict(params)
        out["_meta"] = {"io.modelcontextprotocol/protocolVersion": STATELESS,
                        "io.modelcontextprotocol/clientCapabilities": {}}
        return out

    def _send(self, msg, name=None):
        """POST one message; (headers, the messages in the answer)."""
        req = urllib.request.Request(self.url, data=json.dumps(msg).encode(), method="POST",
                                     headers=self._headers(msg["method"], name))
        try:
            r = self._opener.open(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            # The body of an error answer stays unread: a server may quote
            # the request in it, token and all.
            e.close()
            raise HTTPStatus(e.code) from None
        except (urllib.error.URLError, OSError) as e:
            raise UpstreamError("upstream unreachable: %s" % type(e).__name__) from None
        with r:
            if r.status == 202 or "id" not in msg:
                return r.headers, []
            ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype == "application/json":
                body = r.read(MAX_RESPONSE + 1)
                if len(body) > MAX_RESPONSE:
                    raise UpstreamError("upstream answer over %d bytes" % MAX_RESPONSE)
                try:
                    got = json.loads(body)
                except ValueError:
                    raise UpstreamError("upstream answer is not JSON") from None
                return r.headers, got if isinstance(got, list) else [got]
            if ctype == "text/event-stream":
                return r.headers, self._events(r, msg["id"])
            raise UpstreamError("upstream answered with %s" % (ctype or "no content type"))

    @staticmethod
    def _events(stream, want):
        """The messages of an event stream, up to the answer to `want`. The
        server may keep the stream open after it, so reading stops there."""
        out, data, seen = [], [], 0
        while True:
            line = stream.readline(MAX_RESPONSE)
            seen += len(line)
            if seen > MAX_RESPONSE:
                raise UpstreamError("upstream stream over %d bytes" % MAX_RESPONSE)
            end = not line
            line = line.decode("utf-8").rstrip("\r\n")
            if line == "" and data:
                try:
                    m = json.loads("\n".join(data))
                except ValueError:
                    raise UpstreamError("upstream event is not JSON") from None
                data = []
                out.append(m)
                if isinstance(m, dict) and m.get("id") == want and ("result" in m or "error" in m):
                    return out
            elif line.startswith("data:"):
                data.append(line[5:][1:] if line[5:6] == " " else line[5:])
            if end:
                return out

    def _rpc(self, method, params, name=None):
        with self._lock:
            self._id += 1
            msg = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
            headers, messages = self._send(msg, name)
        for m in messages:
            if isinstance(m, dict) and m.get("id") == msg["id"]:
                if "error" in m:
                    err = m["error"] if isinstance(m["error"], dict) else {}
                    raise RPCError(err.get("code"), err.get("message", ""))
                if isinstance(m.get("result"), dict):
                    return m["result"], headers
        raise UpstreamError("no answer to %s" % method)

    # --- the protocol --------------------------------------------------------

    def connect(self):
        """The protocol version the server and this client agreed on."""
        with self._lock:
            if self.version:
                return self.version
            self.version = STATELESS
            try:
                result, _ = self._rpc("server/discover", self._meta({}))
                if STATELESS in (result.get("supportedVersions") or []):
                    return self.version
            except HTTPStatus as e:
                if e.status in (401, 403):
                    self.version = None
                    raise
            except UpstreamError:
                pass
            self.version = None
            result, headers = self._rpc("initialize", {"protocolVersion": HANDSHAKE[0], "capabilities": {},
                                                       "clientInfo": CLIENT_INFO})
            version = result.get("protocolVersion")
            if version not in HANDSHAKE:
                raise UpstreamError("upstream speaks protocol %r" % str(version)[:40])
            self.version = version
            self.session = headers.get("Mcp-Session-Id")
            self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            return version

    def reset(self):
        with self._lock:
            self.version, self.session = None, None

    def _call(self, method, params, name=None):
        self.connect()
        try:
            return self._rpc(method, self._meta(params), name)[0]
        except HTTPStatus as e:
            # A server that forgot the session answers 404; open a new one, once.
            if e.status != 404 or not self.session:
                raise
        self.reset()
        self.connect()
        return self._rpc(method, self._meta(params), name)[0]

    def list_tools(self):
        tools, cursor = [], None
        for _ in range(MAX_PAGES):
            result = self._call("tools/list", {"cursor": cursor} if cursor else {})
            tools += [t for t in result.get("tools") or [] if isinstance(t, dict)]
            cursor = result.get("nextCursor")
            if not cursor:
                return tools
        raise UpstreamError("upstream listed more than %d pages of tools" % MAX_PAGES)

    def call_tool(self, name, arguments):
        return self._call("tools/call", {"name": name, "arguments": arguments}, name)
