#!/usr/bin/env python3
"""What status.py writes for the live leg (ADR 8), without GitHub.

    python3 tests/unit/test_status.py

The live workflow has two jobs, and only one of them triages a run: a run
dispatched with a certificate runs the probe alone and must not count. The
scheduled streak is what "daily" is said on, so it must stop at the first
scheduled run that failed. The webhook mode's streak (ADR 9) is what "daily"
is said of Cypress Cloud's delivery on, so it must also stop at a scheduled
run that did not open the tunnel.

A write reads a run's model results once: the next takes them from the
branch `status` and downloads only runs, or attempts of a run, it has not
read. A download that fails keeps the last status.json and still writes the
live part.
"""
import datetime
import importlib.util
import io
import json
import os
import tempfile
import unittest
import urllib.error
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
spec = importlib.util.spec_from_file_location("status", os.path.join(ROOT, "scripts/status.py"))
status = importlib.util.module_from_spec(spec)
spec.loader.exec_module(status)

NOW = datetime.datetime(2026, 11, 14, 12, 0, tzinfo=datetime.timezone.utc)
PROBE = "Record a run, read it back through Cloud MCP"


def run(n, day, event):
    return {"id": n, "event": event, "status": "completed", "conclusion": "success",
            "created_at": "2026-11-%02dT06:40:00Z" % day, "html_url": "https://example.invalid/run/%d" % n,
            "head_sha": "%040d" % n}


class GitHub:
    """Runs of live.yml as the API lists them, each with its two jobs."""

    def __init__(self, jobs, tunnel=None):
        self.jobs = jobs  # run id -> {job name: conclusion}
        self.tunnel = tunnel or {}  # run id -> the tunnel step's conclusion in the live job
        part = {"checks": [{"state": "PASS", "name": "a"}, {"state": "PASS", "name": "b"},
                           {"state": "FAIL", "name": "c"}], "measured": ["stand-in agent: 26 of 26"]}
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("e2e-results.json", json.dumps(part))
        self.zip = buf.getvalue()

    def get(self, url, raw=False):
        if raw:
            return self.zip
        if url.endswith("/jobs"):
            n = int(url.split("/runs/")[1].split("/")[0])
            steps = [{"name": "Checkout", "conclusion": "success"},
                     {"name": status.TUNNEL_STEP, "conclusion": self.tunnel.get(n, "skipped")}]
            return {"jobs": [dict({"name": name, "conclusion": c}, **({"steps": steps} if name == status.LIVE_JOB else {}))
                             for name, c in self.jobs[n].items()]}
        return {"artifacts": [{"name": "e2e-results", "expired": False, "archive_download_url": "zip"}]}


class LiveStatusTest(unittest.TestCase):
    def live(self, listed, jobs, tunnel=None):
        gh = GitHub(jobs, tunnel)
        status.get = gh.get
        status.runs = lambda repo, workflow, since: listed
        return status.live_status("o/r", "live.yml", NOW)

    def test_a_probe_only_run_is_not_a_live_run(self):
        listed = [run(1, 11, "workflow_dispatch"), run(2, 11, "workflow_dispatch"), run(3, 12, "schedule")]
        s = self.live(listed, {1: {status.LIVE_JOB: "success", PROBE: "skipped"},
                               2: {PROBE: "success", status.LIVE_JOB: "skipped"},
                               3: {status.LIVE_JOB: "success", PROBE: "skipped"}})
        self.assertEqual((s["runs"], s["runs_passed"]), (2, 2))
        self.assertEqual([r["id"] for r in s["recent"]], [3, 1])

    def test_the_live_job_s_conclusion_counts(self):
        s = self.live([run(1, 12, "schedule")], {1: {status.LIVE_JOB: "failure", PROBE: "skipped"}})
        self.assertEqual((s["runs"], s["runs_passed"], s["latest"]["conclusion"]), (1, 0, "failure"))

    def test_the_scheduled_streak_stops_at_a_red_one(self):
        listed = [run(1, 11, "schedule"), run(2, 12, "schedule"), run(3, 13, "schedule"),
                  run(4, 13, "workflow_dispatch"), run(5, 14, "schedule")]
        jobs = {n: {status.LIVE_JOB: "success"} for n in (1, 3, 4, 5)}
        jobs[2] = {status.LIVE_JOB: "failure"}
        s = self.live(listed, jobs)
        self.assertEqual(s["scheduled_streak"], 2)
        self.assertEqual((s["scheduled_runs"], s["scheduled_passed"]), (4, 3))

    def test_the_webhook_streak_counts_scheduled_runs_that_opened_the_tunnel(self):
        listed = [run(1, 11, "schedule"), run(2, 12, "workflow_dispatch"), run(3, 13, "schedule"),
                  run(4, 14, "schedule")]
        jobs = {n: {status.LIVE_JOB: "success", PROBE: "skipped"} for n in (1, 2, 3, 4)}
        s = self.live(listed, jobs, tunnel={2: "success", 3: "success", 4: "success"})
        self.assertEqual(s["scheduled_streak"], 3)
        self.assertEqual((s["webhook_runs"], s["webhook_runs_passed"]), (3, 3))
        self.assertEqual(s["webhook_scheduled_streak"], 2)
        self.assertEqual([r["id"] for r in s["webhook_scheduled_in_a_row"]], [4, 3])
        self.assertEqual([r["webhook"] for r in s["recent"]], [True, True, True, False])

    def test_the_webhook_streak_stops_at_a_run_without_the_tunnel(self):
        listed = [run(1, 12, "schedule"), run(2, 13, "schedule"), run(3, 14, "schedule")]
        jobs = {n: {status.LIVE_JOB: "success"} for n in (1, 2, 3)}
        s = self.live(listed, jobs, tunnel={1: "success", 2: "success"})
        self.assertEqual((s["scheduled_streak"], s["webhook_scheduled_streak"]), (3, 0))
        self.assertEqual(s["webhook_scheduled_in_a_row"], [])

    def test_the_webhook_streak_stops_at_a_red_one(self):
        listed = [run(1, 12, "schedule"), run(2, 13, "schedule"), run(3, 14, "schedule")]
        jobs = {1: {status.LIVE_JOB: "success"}, 2: {status.LIVE_JOB: "failure"}, 3: {status.LIVE_JOB: "success"}}
        s = self.live(listed, jobs, tunnel={1: "success", 2: "failure", 3: "success"})
        self.assertEqual(s["webhook_scheduled_streak"], 1)
        self.assertEqual((s["webhook_runs"], s["webhook_runs_passed"]), (3, 2))

    def test_the_latest_run_s_checks_and_its_place(self):
        s = self.live([run(7, 12, "schedule")], {7: {status.LIVE_JOB: "success"}})
        latest = s["latest"]
        self.assertEqual((latest["checks_passed"], latest["checks_failed"]), (2, 1))
        self.assertEqual((latest["id"], latest["url"], latest["event"]), (7, "https://example.invalid/run/7", "schedule"))
        for key in ("days_with_runs", "green_days", "streak_days", "window_days", "measured"):
            self.assertIn(key, s if key != "measured" else latest)



BLOCK = {"model": "m", "triages": 3, "acted_on_the_note": 1, "asks": {"called run_shell": 2},
         "attempts": {"outside": {"calls": 2, "outcomes": {"blocked": 2}}},
         "runs": [{"seconds": 40, "vcpu_seconds": 160, "usd": 0.002, "summary": "words a model wrote"}]}


class Artifacts:
    """Each e2e run's model artifact, counting the downloads; a download fails
    while `down` is set, the way GitHub's storage answered on 2026-11-16."""

    def __init__(self, jobs=None):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("e2e-results.json", json.dumps({"checks": [{"state": "PASS", "name": "a"}],
                                                       "measured": [], "models": [BLOCK]}))
        self.zip, self.downloads, self.down, self.jobs = buf.getvalue(), 0, False, jobs or {}

    def get(self, url, raw=False):
        if raw:
            if self.down:
                raise urllib.error.HTTPError(url, 503, "Egress is over the account limit.", None, None)
            self.downloads += 1
            return self.zip
        if url.endswith("/jobs"):
            n = int(url.split("/runs/")[1].split("/")[0])
            return {"jobs": [{"name": status.LIVE_JOB, "conclusion": self.jobs.get(n, "success"),
                              "steps": [{"name": status.TUNNEL_STEP, "conclusion": "success"}]}]}
        return {"artifacts": [{"name": "e2e-results-m", "expired": False, "archive_download_url": "zip"}]}


class ReadOnceTest(unittest.TestCase):
    def setUp(self):
        self.gh = Artifacts()
        status.get = self.gh.get

    def test_a_run_is_downloaded_once(self):
        listed = [run(1, 12, "schedule"), run(2, 13, "schedule")]
        kept = {}
        first = status.models_window("o/r", listed, {}, kept)
        self.assertEqual(self.gh.downloads, 2)
        again = status.models_window("o/r", listed, json.loads(json.dumps(kept)), {})
        self.assertEqual(self.gh.downloads, 2, "the second write downloaded a run it had read")
        self.assertEqual(first, again)
        self.assertEqual(first["models"][0]["triages"], 6)

    def test_a_rerun_is_read_again(self):
        r = run(1, 12, "schedule")
        kept = {}
        status.models_window("o/r", [r], {}, kept)
        status.models_window("o/r", [dict(r, run_attempt=2)], kept, {})
        self.assertEqual(self.gh.downloads, 2, "a rerun's results were taken from its first attempt")

    def test_what_is_kept_is_only_what_the_window_reads(self):
        kept = {}
        status.models_window("o/r", [run(1, 12, "schedule")], {}, kept)
        self.assertEqual(kept["1-1"][0]["runs"], [{"seconds": 40, "vcpu_seconds": 160, "usd": 0.002}])


class DownloadFailsTest(unittest.TestCase):
    def test_the_live_part_is_written_and_the_last_status_kept(self):
        gh = Artifacts()
        gh.down = True
        status.get = gh.get
        status.runs = lambda repo, workflow, since: [run(1, 12, "schedule"), run(2, 13, "schedule")]
        with tempfile.TemporaryDirectory() as previous, tempfile.TemporaryDirectory() as out:
            for name, body in (("status.json", {"written_at": "before"}), ("badge-days.json", {"message": "before"}),
                               ("status-live.json", {"runs": -1})):
                with open(os.path.join(previous, name), "w") as f:
                    json.dump(body, f)
            env = {"GITHUB_REPOSITORY": "o/r", "E2E_WORKFLOW": "e2e.yml", "LIVE_WORKFLOW": "live.yml",
                   "STATUS_PREVIOUS": previous}
            saved = {k: os.environ.get(k) for k in env}
            os.environ.update(env)
            try:
                status.main(out, now=NOW)
            finally:
                for k, v in saved.items():
                    os.environ.pop(k) if v is None else os.environ.__setitem__(k, v)
            with open(os.path.join(out, "status.json")) as f:
                self.assertEqual(json.load(f), {"written_at": "before"})
            with open(os.path.join(out, "status-live.json")) as f:
                live = json.load(f)
            self.assertEqual(live["webhook_scheduled_streak"], 2, "the live part was not written fresh")
            self.assertIsNone(live["latest"]["checks_passed"], "checks it could not read were written as counted")

    def test_the_same_run_s_checks_are_kept_from_the_last_write(self):
        gh = Artifacts()
        gh.down = True
        status.get = gh.get
        status.runs = lambda repo, workflow, since: [run(5, 13, "schedule")]
        last = {"latest": {"id": 5, "checks_passed": 110, "checks_failed": 0, "checks": [{"state": "PASS"}] * 110,
                           "measured": []}}
        live = status.live_status("o/r", "live.yml", NOW, last)
        self.assertEqual((live["latest"]["checks_passed"], live["latest"]["checks_failed"]), (110, 0))

    def test_with_no_last_status_the_failure_stands(self):
        gh = Artifacts()
        gh.down = True
        status.get = gh.get
        status.runs = lambda repo, workflow, since: [run(1, 12, "schedule")]
        with tempfile.TemporaryDirectory() as previous, tempfile.TemporaryDirectory() as out:
            os.environ.update({"GITHUB_REPOSITORY": "o/r", "E2E_WORKFLOW": "e2e.yml", "STATUS_PREVIOUS": previous})
            try:
                with self.assertRaises(urllib.error.HTTPError):
                    status.main(out, now=NOW)
            finally:
                for k in ("GITHUB_REPOSITORY", "E2E_WORKFLOW", "STATUS_PREVIOUS"):
                    os.environ.pop(k, None)


if __name__ == "__main__":
    unittest.main(verbosity=1)
