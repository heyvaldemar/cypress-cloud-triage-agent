#!/usr/bin/env python3
"""What the receiver refuses, sent to the running receiver.

    tests/e2e/webhook.py before URL BODY_FILE < signing-secret
    tests/e2e/webhook.py after URL BODY_FILE DELIVERY_FILE < signing-secret

`before` runs ahead of the real delivery: forged, unsigned, altered, stale
and malformed requests, each refused, and none may start a triage. `after`
runs once scripts/send-run.py has started one, from the delivery it wrote:
the exact delivery again, the same with a new X-Cypress-Event-Id (a header
the signature does not cover), and a retry with a new timestamp. None may
start a second triage. tests/unit/test_receiver.py holds the same cases
against the code; this holds them against the deployed door.

One JSON line per request; exit status 1 if any answer differs.
"""
import hashlib
import hmac
import json
import sys
import time
import urllib.error
import urllib.request

failed = 0


def post(url, raw, headers):
    req = urllib.request.Request(url, data=raw, method="POST", headers=dict(headers, **{"Content-Type": "application/json"}))
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}").get("decision", "")
    except urllib.error.HTTPError as e:
        with e:
            raw = e.read()
        try:
            return e.code, json.loads(raw or b"{}").get("decision", "")
        except ValueError:
            return e.code, ""


def signed(secret, raw, ts=None, **extra):
    ts = str(int(time.time())) if ts is None else str(ts)
    h = {"X-Cypress-Event": "run.completed", "X-Cypress-Event-Id": "e2e-" + ts, "X-Cypress-Event-Version": "1",
         "X-Cypress-Timestamp": ts,
         "X-Cypress-Signature": "sha256=" + hmac.new(secret, ts.encode() + b"." + raw, hashlib.sha256).hexdigest()}
    h.update(extra)
    return h


def expect(check, want, got):
    global failed
    ok = want == got
    failed += 0 if ok else 1
    print(json.dumps({"agent": "webhook", "check": check, "expected": "HTTP %d" % want[0] + (" " + want[1] if want[1] else ""),
                      "got": "HTTP %d" % got[0] + (" " + got[1] if got[1] else ""), "ok": ok}), flush=True)


def before(url, body, secret):
    raw = json.dumps(body, separators=(",", ":")).encode()
    now = int(time.time())
    h = signed(secret, raw)
    del h["X-Cypress-Signature"]
    expect("an unsigned delivery", (401, "unsigned"), post(url, raw, h))
    expect("a forged signature", (401, "bad_signature"),
           post(url, raw, signed(secret, raw, **{"X-Cypress-Signature": "sha256=" + "0" * 64})))
    expect("a delivery signed with another secret", (401, "bad_signature"),
           post(url, raw, signed(b"not-the-signing-secret-at-all", raw)))
    other = dict(body, runNumber=body["runNumber"] + 1,
                 runUrl="https://cloud.cypress.io/projects/%s/runs/%d" % (body["projectId"], body["runNumber"] + 1))
    expect("a body changed after it was signed", (401, "bad_signature"),
           post(url, json.dumps(other, separators=(",", ":")).encode(), signed(secret, raw)))
    expect("a delivery signed six minutes ago", (400, "stale"), post(url, raw, signed(secret, raw, ts=now - 360)))
    expect("a delivery dated six minutes ahead", (400, "stale"), post(url, raw, signed(secret, raw, ts=now + 360)))
    expect("a signed timestamp that is not a number (NaN)", (400, "bad_timestamp"),
           post(url, raw, signed(secret, raw, ts="NaN")))
    stranger = dict(body, projectId="zzz999", runUrl="https://cloud.cypress.io/projects/zzz999/runs/%d" % body["runNumber"])
    raw2 = json.dumps(stranger, separators=(",", ":")).encode()
    expect("a signed run of a project this door does not serve", (403, "unknown_project"),
           post(url, raw2, signed(secret, raw2)))
    passed = dict(body, status="passed", runNumber=body["runNumber"] + 1000,
                  runUrl="https://cloud.cypress.io/projects/%s/runs/%d" % (body["projectId"], body["runNumber"] + 1000))
    raw3 = json.dumps(passed, separators=(",", ":")).encode()
    expect("a signed run that passed", (200, "nothing_to_triage"), post(url, raw3, signed(secret, raw3)))
    a11y = {"runNumber": body["runNumber"], "projectId": body["projectId"], "status": "failed",
            "runUrl": body["runUrl"], "accessibility": {"status": "processed"}}
    raw4 = json.dumps(a11y, separators=(",", ":")).encode()
    expect("an accessibility body sent as run.completed", (202, "ignored"), post(url, raw4, signed(secret, raw4)))
    big = b'{"pad":"' + b"x" * (70 * 1024) + b'"}'
    expect("a body larger than 64 KiB", (413, ""), post(url, big, signed(secret, big)))


def after(url, delivery_file, secret):
    with open(delivery_file, encoding="utf-8") as f:
        d = json.load(f)
    raw, headers = d["body"].encode(), d["headers"]
    expect("the same delivery again", (409, "replayed"), post(url, raw, headers))
    expect("the same delivery with a new X-Cypress-Event-Id", (409, "replayed"),
           post(url, raw, dict(headers, **{"X-Cypress-Event-Id": "a-new-id", "X-Cypress-Request-Id": "a-new-id"})))
    time.sleep(1.1)
    expect("a retry of the same run, signed with a new timestamp", (200, "duplicate"), post(url, raw, signed(secret, raw)))


def main():
    phase, url, body_file = sys.argv[1:4]
    secret = sys.stdin.readline().strip().encode()
    with open(body_file, encoding="utf-8") as f:
        body = json.load(f)
    if phase == "before":
        before(url, body, secret)
    else:
        after(url, sys.argv[4], secret)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
