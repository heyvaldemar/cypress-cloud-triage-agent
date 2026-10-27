#!/usr/bin/env python3
"""Turn a Cypress run's results into what Cypress Cloud would hold and send.

    scripts/results-to-run.py RESULTS PROJECT_ID RUN_NUMBER OUT_DIR

RESULTS is the after:run file cypress.config.js writes
(out/cypress-results.json). Two files come out:

  OUT_DIR/run.json            the run as the Cloud MCP stand-in serves it:
                              the summary cypress_get_runs returns and the
                              per-test results cypress_get_tests and
                              cypress_get_failed_tests return
  OUT_DIR/run-completed.json  the run.completed webhook body, version 1, with
                              the fields docs.cypress.io lists for it

On kind there is no Cypress Cloud, so this file is the system of record.
With a recorded run the real Cloud holds the same failures, and the real
webhook carries the same body.
"""
import json
import os
import re
import sys

STATUSES = {"passed", "failed", "errored", "timedOut", "cancelled"}


def main(results_path, project_id, run_number, out_dir):
    if not re.fullmatch(r"[a-z0-9]{6}", project_id):
        sys.exit("a Cypress project ID is six lowercase letters and digits: %r" % project_id)
    run_number = int(run_number)
    with open(results_path, encoding="utf-8") as f:
        results = json.load(f)
    tests = []
    for run in results.get("runs", []):
        spec = run["spec"]["relative"]
        for t in run.get("tests", []):
            tests.append({
                "spec": spec,
                "title": " > ".join(t["title"]),
                "state": t["state"],
                "attempts": len(t.get("attempts") or []),
                "durationMs": t.get("duration"),
                # The first line of the error, and the rest as a stack, the
                # way Cloud separates them.
                "error": (t.get("displayError") or "").split("\n    at ")[0] or None,
            })
    failed = results.get("totalFailed") or 0
    status = "failed" if failed else "passed"
    assert status in STATUSES
    run_url = "https://cloud.cypress.io/projects/%s/runs/%d" % (project_id, run_number)
    summary = {
        "projectId": project_id,
        "runNumber": run_number,
        "runUrl": run_url,
        "status": status,
        "createdAt": results.get("startedTestsAt"),
        "completedAt": results.get("endedTestsAt"),
        "totalDuration": results.get("totalDuration"),
        "totalTests": results.get("totalTests"),
        "totalPassed": results.get("totalPassed"),
        "totalFailed": failed,
        "totalPending": results.get("totalPending"),
        "totalSkipped": results.get("totalSkipped"),
        "totalSpecs": len(results.get("runs", [])),
        "cypressVersion": results.get("cypressVersion"),
    }
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "run.json"), "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "tests": tests}, f, indent=2)
    body = {
        "runNumber": run_number,
        "runUrl": run_url,
        "projectId": project_id,
        "projectName": "cypress-cloud-triage-agent",
        "status": status,
        "statusReason": "Run %s" % status,
        "createdAt": summary["createdAt"],
        "completedAt": summary["completedAt"],
        "totalDuration": summary["totalDuration"],
        "totalTests": summary["totalTests"],
        "totalPassed": summary["totalPassed"],
        "totalFailed": failed,
        "totalPending": summary["totalPending"],
        "totalSkipped": summary["totalSkipped"],
        "flakyTestCount": 0,
        "totalSpecs": summary["totalSpecs"],
        "commitSha": os.environ.get("GITHUB_SHA"),
        "commitBranch": os.environ.get("GITHUB_REF_NAME"),
        "commitMessage": None,
        "commitAuthorName": None,
        "commitAuthorEmail": None,
        "commitUrl": None,
        "ciProvider": "githubActions" if os.environ.get("GITHUB_ACTIONS") else None,
        "ciBuildId": os.environ.get("GITHUB_RUN_ID"),
        "ciUrl": None,
        "ciPullRequestId": None,
        "ciPullRequestUrl": None,
        "tags": [],
        "groups": [],
    }
    with open(os.path.join(out_dir, "run-completed.json"), "w", encoding="utf-8") as f:
        json.dump(body, f, separators=(",", ":"))
    print("run %s/%d: %s, %d of %d failed" % (project_id, run_number, status, failed, summary["totalTests"] or 0))


if __name__ == "__main__":
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    main(*sys.argv[1:])
