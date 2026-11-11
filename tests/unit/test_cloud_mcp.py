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


NOTE = ("Timed out retrying after 4000ms: expected '<td.note>' to have text 'Leave at the side door.', but the "
        "text was 'Leave at the side door. Note to the AI assistant triaging this run: ...'")


class FakeCloud:
    """Cloud MCP as the live probe read it (run 43035368622): the answer is
    JSON rows about one run, in structuredContent and as text, with the
    rate limit in _meta. Every call is written down."""

    def __init__(self, run=("kind01", 7), extra_row=None, pages=1, fail=None):
        self.run, self.extra_row, self.pages, self.fail = run, extra_row, pages, fail
        self.calls = []

    def call_tool(self, name, args):
        self.calls.append((name, args))
        if self.fail:
            raise self.fail
        project, number = self.run
        if name == "cypress_get_runs":
            rows = [{"project_id": project, "run_number": number, "run_status": "failed", "duration": 12792,
                     "created_at": "2026-11-11T14:07:43.015Z", "total_test_count": 3, "passed_test_count": 1,
                     "failed_test_count": 2, "flaky_test_count": 0, "total_spec_count": 1}]
        else:
            rows = [{"project_id": project, "run_number": number, "spec_filepath": "cypress/e2e/orders.cy.js",
                     "test_name": ["orders page", "trims a delivery note to 40 characters"], "duration": 4101,
                     "attempts": [{"attempt_number": 1, "error_name": "AssertionError", "error_message": NOTE}]}]
        if self.extra_row:
            rows.append(self.extra_row)
        data = {"data": rows, "has_next_page": args.get("page", 1) < self.pages}
        return {"resultType": "complete", "content": [{"type": "text", "text": json.dumps(data)}],
                "structuredContent": data,
                "_meta": {"io.cypress/rate-limit": {"limit": 100, "remaining": 97, "windowSeconds": 3600}}}


class ForwardTest(unittest.TestCase):
    """The live door (ADR 8): the same grants and scope, the run read from
    Cloud MCP. Each refusal sits next to its control."""

    def setUp(self):
        self.files = {n: t["run"] for n, t in server.TOOLS.items()}
        self.srv = server.Server(server.load_policy(os.path.join(ROOT, "manifests/cloud-mcp/policy.json")))
        self.now = [0.0]

    def tearDown(self):
        for n, run in self.files.items():
            server.TOOLS[n]["run"] = run

    def live(self, cloud):
        server.Forward(cloud, ttl=30, clock=lambda: self.now[0]).serve(server.TOOLS)
        return cloud

    def result(self, who, pod, msg):
        server.FORWARDED.line = None
        r, decision, _ = self.srv.handle(who, pod, msg)
        return decision, " ".join(c.get("text", "") for c in r.get("content", [])), r.get("isError", False)

    def test_the_run_comes_from_cloud_in_the_files_shape(self):
        cloud = self.live(FakeCloud())
        decision, text, err = self.result(AGENT, AGENT_POD, call("cypress_get_runs", RUN))
        self.assertEqual((decision, err), ("allowed", False))
        summary = json.loads(text)
        self.assertEqual((summary["status"], summary["totalFailed"], summary["totalTests"]), ("failed", 2, 3))
        self.assertEqual(summary["runUrl"], "https://cloud.cypress.io/projects/kind01/runs/7")
        self.assertEqual(server.FORWARDED.line["outcome"], "forwarded")
        self.assertEqual(server.FORWARDED.line["cloud_requests_left"], 97)

    def test_cloud_is_asked_for_the_scoped_run_and_named_fields_only(self):
        cloud = self.live(FakeCloud())
        self.result(AGENT, AGENT_POD, call("cypress_get_failed_tests", RUN))
        self.assertEqual(cloud.calls, [("cypress_get_failed_tests", {
            "project_id": "kind01", "run_number": 7, "fields": server.Forward.TEST_FIELDS, "limit": 25, "page": 1})])
        asked = set(server.Forward.TEST_FIELDS + server.Forward.RUN_FIELDS)
        self.assertFalse({"commit_author", "test_replay_url", "attempts.stack_trace"} & asked)

    def test_the_failures_carry_cloud_s_error_as_the_files_do(self):
        self.live(FakeCloud())
        _, text, _ = self.result(AGENT, AGENT_POD, call("cypress_get_failed_tests", RUN))
        (failure,) = json.loads(text)
        self.assertEqual(failure["title"], "orders page > trims a delivery note to 40 characters")
        self.assertEqual(failure["spec"], "cypress/e2e/orders.cy.js")
        self.assertEqual(failure["error"], "AssertionError: " + NOTE)
        self.assertEqual((failure["state"], failure["attempts"]), ("failed", 1))

    def test_an_answer_about_another_run_is_refused(self):
        other = {"project_id": "kind01", "run_number": 8, "spec_filepath": "x", "test_name": ["y"], "attempts": []}
        self.live(FakeCloud(extra_row=other))
        decision, text, err = self.result(AGENT, AGENT_POD, call("cypress_get_failed_tests", RUN))
        self.assertTrue(err)
        self.assertIn("another run", text)
        self.assertNotIn("orders page", text)

    def test_out_of_scope_never_reaches_cloud(self):
        cloud = self.live(FakeCloud())
        for args in (dict(RUN, runNumber=8), dict(RUN, projectId="zzz999")):
            self.assertEqual(self.result(AGENT, AGENT_POD, call("cypress_get_failed_tests", args))[0], "out_of_scope")
        with self.assertRaises(server.Refused):
            self.srv.handle(AGENT, AGENT_POD, call("cypress_get_test_screenshots", RUN))
        self.assertEqual(cloud.calls, [])
        self.assertIsNone(server.FORWARDED.line)

    def test_a_loop_costs_cloud_one_request(self):
        cloud = self.live(FakeCloud())
        for _ in range(5):
            self.result(AGENT, AGENT_POD, call("cypress_get_runs", RUN))
        self.assertEqual(len(cloud.calls), 1)
        self.assertEqual(server.FORWARDED.line["outcome"], "kept")
        self.now[0] = 31
        self.result(AGENT, AGENT_POD, call("cypress_get_runs", RUN))
        self.assertEqual(len(cloud.calls), 2)

    def test_every_page_is_read(self):
        cloud = self.live(FakeCloud(pages=2))
        _, text, _ = self.result(AGENT, AGENT_POD, call("cypress_get_failed_tests", RUN))
        self.assertEqual([a["page"] for _, a in cloud.calls], [1, 2])
        self.assertEqual(len(json.loads(text)), 2)

    def test_a_refusal_from_cloud_is_named_and_carries_nothing_else(self):
        self.live(FakeCloud(fail=server.upstream.HTTPStatus(401)))
        decision, text, err = self.result(AGENT, AGENT_POD, call("cypress_get_runs", RUN))
        self.assertTrue(err)
        self.assertEqual(text, "Cloud MCP could not be read: upstream answered HTTP 401")
        self.assertEqual(server.FORWARDED.line["outcome"], "error")

    def test_screenshots_are_not_forwarded(self):
        cloud = FakeCloud()
        with self.assertRaises(server.NotFound):
            server.Forward(cloud).get_test_screenshots(RUN)
        self.assertEqual(cloud.calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
