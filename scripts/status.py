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
                            and measurements, what each model in the model
                            leg did in it (ADR 6), and models_window: each
                            model's counts across every run in the window
    badge-days.json         shields.io endpoint: green days in the window
    badge-checks.json       shields.io endpoint: the latest run's checks
    badge-<measure>.json    one per measurement the run printed
    status-live.json        with LIVE_WORKFLOW set (live.yml, ADR 8): the same
                            window for the runs whose live job ran, the
                            streak of scheduled ones, and the latest run's
                            checks; a run that ran only the probe is not one

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
# The live workflow's job that triages a recorded run; its other job, the
# probe, triages nothing.
LIVE_JOB = "A recorded run, triaged through the real Cloud MCP"


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
    merged = {"checks": [], "measured": [], "models": []}
    for a in arts:
        z = zipfile.ZipFile(io.BytesIO(get(a["archive_download_url"], raw=True)))
        for name in z.namelist():
            if name.endswith(".json"):
                part = json.loads(z.read(name))
                job = a["name"][len("e2e-results"):].strip("-")
                for c in part.get("checks", []):
                    merged["checks"].append(dict(c, job=job) if job else c)
                merged["measured"] += part.get("measured", [])
                # The model leg's jobs (ADR 6) add what each model did, by triage.
                merged["models"] += part.get("models", [])
    return merged


def median(values):
    return sorted(values)[len(values) // 2] if values else None


def models_window(repo, finished):
    """What each model in the model leg (ADR 6) did across every finished run
    on main in the window, not only the latest. One run is one sample of three
    triages a model, and the counts move from run to run (on 2026-11-08
    Granite-4.0-1B acted on the note in 1 of 3 in one run and 0 of 3 in the
    next): a claim about a model rests on the count here, with its runs."""
    per, sampled = {}, []
    for r in finished:
        arts = [a for a in get(f"/repos/{repo}/actions/runs/{r['id']}/artifacts")["artifacts"]
                if a["name"].startswith("e2e-results-") and not a["expired"]]
        blocks = []
        for a in arts:
            z = zipfile.ZipFile(io.BytesIO(get(a["archive_download_url"], raw=True)))
            for name in z.namelist():
                if name.endswith(".json"):
                    blocks += json.loads(z.read(name)).get("models", [])
        if not blocks:
            continue
        sampled.append(r)
        for b in blocks:
            m = per.setdefault(b["model"], {"model": b["model"], "runs": 0, "triages": 0, "acted_on_the_note": 0,
                                            "asks": {}, "attempts": {}, "_seconds": [], "_vcpu_seconds": [], "_usd": []})
            m["runs"] += 1
            m["triages"] += b.get("triages", 0)
            m["acted_on_the_note"] += b.get("acted_on_the_note", 0)
            for ask, n in b.get("asks", {}).items():
                m["asks"][ask] = m["asks"].get(ask, 0) + n
            for kind, t in b.get("attempts", {}).items():
                a = m["attempts"].setdefault(kind, {"calls": 0, "outcomes": {}})
                a["calls"] += t.get("calls", 0)
                for outcome, n in t.get("outcomes", {}).items():
                    a["outcomes"][outcome] = a["outcomes"].get(outcome, 0) + n
            for t in b.get("runs", []):
                for key in ("seconds", "vcpu_seconds", "usd"):
                    if isinstance(t.get(key), (int, float)):
                        m["_" + key].append(t[key])
    models = []
    for m in sorted(per.values(), key=lambda m: m["model"]):
        for key in ("seconds", "vcpu_seconds", "usd"):
            m["median_" + key] = median(m.pop("_" + key))
        m["line"] = "%s: acted on the note in %d of %d triages across %d runs" % (
            m["model"], m["acted_on_the_note"], m["triages"], m["runs"])
        models.append(m)

    def ref(r):
        return {"id": r["id"], "at": r["created_at"], "url": r["html_url"], "conclusion": r["conclusion"]}

    return {"window_days": WINDOW_DAYS, "runs": len(sampled),
            "runs_failed": sum(1 for r in sampled if r["conclusion"] != "success"),
            "first": ref(sampled[0]) if sampled else None, "last": ref(sampled[-1]) if sampled else None,
            "models": models}


def days_of(finished):
    """Days with a run, the green ones (a day's last run passed), the streak."""
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
    return days, green, streak


def live_status(repo, workflow, now):
    """The live leg (ADR 8): every run on main in the window whose live job
    ran, by that job's conclusion. A run dispatched with a certificate runs
    the probe only and is left out. scheduled_streak is the scheduled runs
    that passed, in a row, back from the latest scheduled one: "daily" is
    said of the live leg when it is two or more."""
    since = (now - datetime.timedelta(days=WINDOW_DAYS - 1)).date()
    finished = []
    for r in runs(repo, workflow, since):
        if r["event"] not in ("schedule", "workflow_dispatch") or r["status"] != "completed":
            continue
        job = next((j for j in get(f"/repos/{repo}/actions/runs/{r['id']}/jobs")["jobs"] if j["name"] == LIVE_JOB),
                   None)
        if job and job.get("conclusion") in ("success", "failure"):
            finished.append(dict(r, conclusion=job["conclusion"]))
    finished.sort(key=lambda r: r["created_at"])
    days, green, streak = days_of(finished)
    scheduled = [r for r in finished if r["event"] == "schedule"]
    in_a_row = 0
    for r in reversed(scheduled):
        if r["conclusion"] != "success":
            break
        in_a_row += 1
    latest = finished[-1] if finished else None
    res = results(repo, latest["id"]) if latest else None
    checks = res["checks"] if res else []

    def ref(r):
        return {"id": r["id"], "at": r["created_at"], "event": r["event"], "conclusion": r["conclusion"],
                "url": r["html_url"], "commit": r["head_sha"][:7]}

    return {
        "repository": repo, "workflow": workflow, "job": LIVE_JOB,
        "written_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "window_days": WINDOW_DAYS,
        "days_with_runs": len(days), "green_days": len(green), "streak_days": streak,
        "runs": len(finished), "runs_passed": sum(1 for r in finished if r["conclusion"] == "success"),
        "scheduled_runs": len(scheduled), "scheduled_passed": sum(1 for r in scheduled if r["conclusion"] == "success"),
        "scheduled_streak": in_a_row,
        "latest": None if not latest else dict(ref(latest), **{
            "checks_passed": sum(1 for c in checks if c.get("state") == "PASS"),
            "checks_failed": sum(1 for c in checks if c.get("state") == "FAIL"),
            "checks": checks, "measured": res["measured"] if res else []}),
        "recent": [ref(r) for r in reversed(finished[-10:])],
    }


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

    days, green, streak = days_of(finished)

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
            "models": res["models"] if res else [],
        },
        # Every run in the window with model-leg results, counted together.
        "models_window": models_window(repo, finished),
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
    if os.environ.get("LIVE_WORKFLOW"):
        live = live_status(repo, os.environ["LIVE_WORKFLOW"], now)
        with open(os.path.join(out, "status-live.json"), "w") as f:
            json.dump(live, f, indent=2)
        print(f"{repo} live: {live['runs_passed']} of {live['runs']} runs, scheduled in a row {live['scheduled_streak']}")
    print(f"{repo}: {len(green)} of {len(days)} days green, streak {streak}, "
          f"latest {latest['conclusion'] if latest else 'none'} with {passed} of {passed + failed} checks")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
