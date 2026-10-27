#!/usr/bin/env python3
"""status.py OUT_DIR - what the e2e workflow has shown lately, as JSON a page
or a badge can read.

    GITHUB_REPOSITORY=owner/name GITHUB_TOKEN=... E2E_WORKFLOW=e2e.yml \\
        scripts/status.py out/status

Reads the e2e workflow's runs on main over the last 30 days (pushes, the
daily schedule, manual runs; never pull requests, never cancelled runs) and
the results the latest finished run uploaded (tests/e2e/run.sh writes them
when E2E_RESULTS is set). Writes:

    status.json             the window, the streak, the latest run's checks
                            and measurements
    badge-days.json         shields.io endpoint: green days in the window
    badge-checks.json       shields.io endpoint: the latest run's checks
    badge-<measure>.json    one per measurement the run printed

A day counts as green when its last finished run passed; the run count beside
it shows the failures that rule hides. The streak counts green days back from
the latest day that had a run. Nothing here is typed
by hand: every number is read from GitHub at the time it is written.
"""
import datetime
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
import zipfile

API = "https://api.github.com"
WINDOW_DAYS = 30


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def get(url, raw=False):
    req = urllib.request.Request(url if url.startswith("http") else API + url)
    req.add_header("Authorization", "Bearer " + os.environ["GITHUB_TOKEN"])
    req.add_header("Accept", "application/vnd.github+json")
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=60) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        # An artifact download answers with a redirect to signed storage,
        # which rejects the API's Authorization header: follow it without.
        if e.code not in (301, 302, 303, 307, 308) or not e.headers.get("Location"):
            raise
        with urllib.request.urlopen(e.headers["Location"], timeout=120) as r:
            body = r.read()
    return body if raw else json.loads(body)


def runs(repo, workflow, since):
    out, page = [], 1
    while True:
        batch = get(f"/repos/{repo}/actions/workflows/{workflow}/runs?branch=main&per_page=100&page={page}"
                    f"&created=%3E%3D{since:%Y-%m-%d}")["workflow_runs"]
        out += batch
        if len(batch) < 100:
            return out
        page += 1


def results(repo, run_id):
    """The latest run's e2e-results artifacts, merged; None when it has none."""
    arts = [a for a in get(f"/repos/{repo}/actions/runs/{run_id}/artifacts")["artifacts"]
            if a["name"].startswith("e2e-results") and not a["expired"]]
    if not arts:
        return None
    merged = {"checks": [], "measured": []}
    for a in arts:
        z = zipfile.ZipFile(io.BytesIO(get(a["archive_download_url"], raw=True)))
        for name in z.namelist():
            if name.endswith(".json"):
                part = json.loads(z.read(name))
                job = a["name"][len("e2e-results"):].strip("-")
                for c in part.get("checks", []):
                    merged["checks"].append(dict(c, job=job) if job else c)
                merged["measured"] += part.get("measured", [])
    return merged


def badge(label, message, color):
    return {"schemaVersion": 1, "label": label, "message": message, "color": color}


def main(out):
    repo, workflow = os.environ["GITHUB_REPOSITORY"], os.environ["E2E_WORKFLOW"]
    now = datetime.datetime.now(datetime.timezone.utc)
    since = (now - datetime.timedelta(days=WINDOW_DAYS - 1)).date()
    finished = [r for r in runs(repo, workflow, since)
                if r["event"] in ("push", "schedule", "workflow_dispatch")
                and r["status"] == "completed" and r["conclusion"] in ("success", "failure")]
    finished.sort(key=lambda r: r["created_at"])

    last_of_day = {}
    for r in finished:
        last_of_day[r["created_at"][:10]] = r["conclusion"]
    days = sorted(last_of_day)
    green = [d for d in days if last_of_day[d] == "success"]
    streak = 0
    for d in reversed(days):
        if last_of_day[d] != "success":
            break
        streak += 1

    latest = finished[-1] if finished else None
    res = results(repo, latest["id"]) if latest else None
    checks = res["checks"] if res else []
    passed = sum(1 for c in checks if c.get("state") == "PASS")
    failed = sum(1 for c in checks if c.get("state") == "FAIL")

    status = {
        "repository": repo,
        "workflow": workflow,
        "written_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_days": WINDOW_DAYS,
        "days_with_runs": len(days),
        "green_days": len(green),
        "streak_days": streak,
        "runs": len(finished),
    "runs_passed": sum(1 for r in finished if r["conclusion"] == "success"),
        "latest": None if not latest else {
            "conclusion": latest["conclusion"],
            "at": latest["created_at"],
            "url": latest["html_url"],
            "commit": latest["head_sha"][:7],
            "checks_passed": passed,
            "checks_failed": failed,
            "checks": checks,
            "measured": res["measured"] if res else [],
        },
    }
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "status.json"), "w") as f:
        json.dump(status, f, indent=2)

    if days:
        color = "brightgreen" if len(green) == len(days) else ("yellow" if len(green) >= 0.9 * len(days) else "red")
        # Both counts: a day is green by its last run, and the runs show
        # what that hides, a failure earlier the same day.
        msg = f"{len(green)} of {len(days)} days green, {status['runs_passed']} of {len(finished)} runs"
    else:
        color, msg = "lightgrey", "no runs yet"
    json.dump(badge(f"e2e, last {WINDOW_DAYS} days", msg, color), open(os.path.join(out, "badge-days.json"), "w"))
    if checks:
        color = "brightgreen" if not failed else "red"
        json.dump(badge("checks", f"{passed} of {passed + failed} passed, {latest['created_at'][:10]}", color),
                  open(os.path.join(out, "badge-checks.json"), "w"))
    for line in status["latest"]["measured"] if latest else []:
        name, _, value = line.partition(": ")
        if value:
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            json.dump(badge(name, value.split(" (")[0], "blue"), open(os.path.join(out, f"badge-{slug}.json"), "w"))
    print(f"{repo}: {len(green)} of {len(days)} days green, streak {streak}, "
          f"latest {latest['conclusion'] if latest else 'none'} with {passed} of {passed + failed} checks")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
