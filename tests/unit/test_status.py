#!/usr/bin/env python3
"""What status.py writes for the live leg (ADR 8), without GitHub.

    python3 tests/unit/test_status.py

The live workflow has two jobs, and only one of them triages a run: a run
dispatched with a certificate runs the probe alone and must not count. The
scheduled streak is what "daily" is said on, so it must stop at the first
scheduled run that failed.
"""
import datetime
import importlib.util
import io
import json
import os
import unittest
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

    def __init__(self, jobs):
        self.jobs = jobs  # run id -> {job name: conclusion}
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
            return {"jobs": [{"name": name, "conclusion": c} for name, c in self.jobs[n].items()]}
        return {"artifacts": [{"name": "e2e-results", "expired": False, "archive_download_url": "zip"}]}


class LiveStatusTest(unittest.TestCase):
    def live(self, listed, jobs):
        gh = GitHub(jobs)
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

    def test_the_latest_run_s_checks_and_its_place(self):
        s = self.live([run(7, 12, "schedule")], {7: {status.LIVE_JOB: "success"}})
        latest = s["latest"]
        self.assertEqual((latest["checks_passed"], latest["checks_failed"]), (2, 1))
        self.assertEqual((latest["id"], latest["url"], latest["event"]), (7, "https://example.invalid/run/7", "schedule"))
        for key in ("days_with_runs", "green_days", "streak_days", "window_days", "measured"):
            self.assertIn(key, s if key != "measured" else latest)


if __name__ == "__main__":
    unittest.main(verbosity=1)
