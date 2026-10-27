#!/usr/bin/env python3
"""The Cloud MCP door's grants and scope, without a cluster.

    python3 tests/unit/test_cloud_mcp.py

Each refusal sits next to its control: the agent reads its own run.
"""
import importlib.util
import json
import os
import shutil
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
spec = importlib.util.spec_from_file_location("server", os.path.join(ROOT, "manifests/cloud-mcp/server.py"))
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

AGENT = "system:serviceaccount:triage-agents:triage-agent"
NOTIFIER = "system:serviceaccount:notify:notifier"
AGENT_POD = "triage-kind01-7-x2b9q"
RUN = {"projectId": "kind01", "runNumber": 7}


def call(name, args):
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}


class DoorTest(unittest.TestCase):
    def setUp(self):
        self.runs = tempfile.mkdtemp()
        for n in (7, 8):
            with open(os.path.join(self.runs, "kind01-%d.json" % n), "w") as f:
                json.dump({"summary": {"runNumber": n, "totalFailed": 2},
                           "tests": [{"title": "t", "state": "failed", "error": "e"},
                                     {"title": "u", "state": "passed", "error": None}]}, f)
        server.RUNS_DIR = self.runs
        self.srv = server.Server(server.load_policy(os.path.join(ROOT, "manifests/cloud-mcp/policy.json")))

    def tearDown(self):
        shutil.rmtree(self.runs)

    def result(self, who, pod, msg):
        r, decision, _ = self.srv.handle(who, pod, msg)
        return decision, " ".join(c.get("text", "") for c in r.get("content", []))

    def test_the_agent_reads_its_own_run(self):
        decision, text = self.result(AGENT, AGENT_POD, call("cypress_get_failed_tests", RUN))
        self.assertEqual(decision, "allowed")
        self.assertEqual(len(json.loads(text)), 1)

    def test_the_agent_lists_two_tools(self):
        r, _, _ = self.srv.handle(AGENT, AGENT_POD, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual([t["name"] for t in r["tools"]], ["cypress_get_failed_tests", "cypress_get_runs"])

    def test_the_agent_cannot_read_another_run(self):
        self.assertEqual(self.result(AGENT, AGENT_POD, call("cypress_get_runs", dict(RUN, runNumber=8)))[0],
                         "out_of_scope")

    def test_the_agent_cannot_read_another_project(self):
        self.assertEqual(self.result(AGENT, AGENT_POD, call("cypress_get_runs", dict(RUN, projectId="zzz999")))[0],
                         "out_of_scope")

    def test_a_pod_that_is_not_a_triage_pod_reads_nothing(self):
        self.assertEqual(self.result(AGENT, "something-else", call("cypress_get_runs", RUN))[0], "out_of_scope")

    def test_the_notifier_reads_any_run_summary(self):
        self.assertEqual(self.result(NOTIFIER, "notifier-abc", call("cypress_get_runs", dict(RUN, runNumber=8)))[0],
                         "allowed")

    def test_a_tool_not_granted_looks_like_no_tool(self):
        for who, name in ((AGENT, "cypress_get_test_screenshots"), (AGENT, "run_shell"),
                          (NOTIFIER, "cypress_get_failed_tests")):
            with self.assertRaises(server.Refused) as e:
                self.srv.handle(who, AGENT_POD, call(name, RUN))
            self.assertEqual(e.exception.message, "Unknown tool: %s" % name)

    def test_a_stranger_has_no_tools(self):
        with self.assertRaises(server.Refused):
            self.srv.handle("system:serviceaccount:default:default", AGENT_POD, call("cypress_get_runs", RUN))

    def test_a_run_number_as_a_string_is_invalid(self):
        self.assertEqual(self.result(AGENT, AGENT_POD, call("cypress_get_runs", dict(RUN, runNumber="7")))[0],
                         "invalid_arguments")

    def test_rate_limit(self):
        decisions = [self.result(AGENT, AGENT_POD, call("cypress_get_runs", RUN))[0] for _ in range(12)]
        self.assertEqual(decisions.count("rate_limited"), 2)

    def test_every_tool_is_read_only(self):
        for name, t in server.TOOLS.items():
            self.assertFalse(t["definition"]["annotations"]["destructiveHint"], name)


if __name__ == "__main__":
    unittest.main(verbosity=1)
