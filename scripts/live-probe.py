#!/usr/bin/env python3
"""Ask the real Cloud MCP what it serves, before the door forwards to it.

    scripts/live-probe.py RESULTS OUT_DIR < token

RESULTS is the after:run file of a recorded run (out/cypress-results.json);
the token is the Cloud MCP personal access token, on stdin. The project ID
is CYPRESS_PROJECT_ID, the run's number comes from the run's URL.

Cloud MCP publishes its tool names but not their arguments or its answers,
so the door's forward is written from what this reads (ADR 8). It connects
with the door's own client (manifests/cloud-mcp/upstream.py), lists the
tools, and calls the four that describe one run with arguments built from
each tool's own input schema.

What it prints, and writes to $GITHUB_STEP_SUMMARY, is public: the protocol
version, each tool's name and input schema, the argument names it used, and
for each answer its size, its shape with every value replaced by its type,
and whether the note's address is in it. The answers themselves can hold
what a public log must not (the project, the people on it), so they go only
to OUT_DIR/probe.json, which the workflow encrypts to the certificate it is
given and otherwise deletes.
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "manifests", "cloud-mcp"))
import upstream  # noqa: E402

URL = os.environ.get("CLOUD_MCP_URL", "https://mcp.cypress.io/mcp")
RUN_URL = re.compile(r"https://cloud\.cypress\.io/projects/([a-z0-9]{6})/runs/([1-9][0-9]{0,8})")
ASKED = ("cypress_get_runs", "cypress_get_failed_tests", "cypress_get_tests", "cypress_get_specs")
NOTE = "collector.outside.svc.cluster.local"


def recorded_run(results_path, log_path):
    with open(results_path, encoding="utf-8") as f:
        url = json.load(f).get("runUrl") or ""
    if not RUN_URL.fullmatch(url) and os.path.exists(log_path):
        with open(log_path, encoding="utf-8", errors="replace") as f:
            m = RUN_URL.search(f.read())
        url = m.group(0) if m else ""
    m = RUN_URL.fullmatch(url)
    if not m:
        sys.exit("no recorded run in %s or %s" % (results_path, log_path))
    return url, m.group(1), int(m.group(2))


def arguments(schema, url, project, number):
    """Arguments for one run, from the tool's own schema: the run's URL where
    it takes one, else its project and number; a limit of 5."""
    props = (schema or {}).get("properties") or {}
    args = {}
    for name, p in props.items():
        low = name.lower().replace("_", "")
        kind = p.get("type") if isinstance(p, dict) else None
        if "url" in low and "run" in low:
            args[name] = url
        elif "runnumber" in low or low in ("run", "runid"):
            args[name] = [number] if kind == "array" else (str(number) if kind == "string" else number)
        elif "project" in low:
            args[name] = project
        elif low == "limit":
            args[name] = 5
    if any("url" in n.lower() and "run" in n.lower() for n in args):
        for n in list(args):
            if n.lower().replace("_", "") in ("runnumber", "projectid") and n not in schema.get("required", []):
                del args[n]
    return args


def shape(value, depth=0):
    """The value with every leaf replaced by its type: what a public log may show."""
    if depth > 6:
        return "..."
    if isinstance(value, dict):
        return {k: shape(v, depth + 1) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [shape(value[0], depth + 1), "x%d" % len(value)] if value else []
    return type(value).__name__


def text_shape(text):
    try:
        return {"json": shape(json.loads(text))}
    except ValueError:
        return {"text_lines": len(text.splitlines())}


def main(results_path, out_dir):
    token = sys.stdin.readline().strip()
    url, project, number = recorded_run(results_path, os.path.join(out_dir, "cypress.log"))
    if project != os.environ.get("CYPRESS_PROJECT_ID", project):
        sys.exit("the recorded run is not in CYPRESS_PROJECT_ID")
    client = upstream.Upstream(URL, token)
    public = {"protocol": client.connect(), "session": bool(client.session)}
    private = {"run": url, "calls": []}
    tools = client.list_tools()
    public["tools"] = [{"name": t.get("name"), "inputSchema": t.get("inputSchema"),
                        "annotations": t.get("annotations")} for t in tools]
    public["calls"] = []
    by_name = {t.get("name"): t for t in tools}
    for name in ASKED:
        if name not in by_name:
            public["calls"].append({"tool": name, "listed": False})
            continue
        args = arguments(by_name[name].get("inputSchema"), url, project, number)
        try:
            result = client.call_tool(name, args)
        except upstream.UpstreamError as e:
            public["calls"].append({"tool": name, "arguments": sorted(args), "error": str(e)})
            continue
        content = result.get("content") or []
        texts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
        joined = "\n".join(texts)
        public["calls"].append({
            "tool": name, "arguments": sorted(args), "isError": bool(result.get("isError")),
            "content_types": sorted({c.get("type") for c in content if isinstance(c, dict)}),
            "characters": len(joined), "shape": [text_shape(t) for t in texts],
            "structured": shape(result.get("structuredContent")) if "structuredContent" in result else None,
            "the_note_is_in_it": NOTE in joined,
            "the_run_number_is_in_it": re.search(r"\b%d\b" % number, joined) is not None,
        })
        private["calls"].append({"tool": name, "arguments": args, "result": result})
    with open(os.path.join(out_dir, "probe.json"), "w", encoding="utf-8") as f:
        json.dump({"public": public, "private": private}, f, indent=2)
    # An upstream error message may quote the run; the public part never names it.
    text = json.dumps(public, indent=2).replace(url, "<run>").replace(project, "<project>")
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## What Cloud MCP serves\n\n```json\n%s\n```\n" % text)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    sys.exit(main(*sys.argv[1:]))
