#!/usr/bin/env python3
"""The CI trigger: send a finished run to the receiver, signed as Cypress Cloud signs.

    scripts/send-run.py URL BODY_FILE [DELIVERY_FILE] < signing-secret

Cypress Cloud sends run.completed only on a plan with webhooks. Until then
the last step of the CI job that ran Cypress sends the same body, with the
same headers and the same signature: HMAC-SHA256 of "<timestamp>.<body>"
under the webhook's signing secret, as "sha256=<hex>". The receiver cannot
tell the two apart, which is the point: when the project gets webhooks, the
step is deleted and nothing else changes.

The secret is read from stdin, never from an argument or the environment.
DELIVERY_FILE, if given, gets the headers and the signature that were sent
(never the secret), so a test can replay the exact delivery.

Exit status 0 when the receiver accepted the run (2xx), 1 otherwise.
"""
import hashlib
import hmac
import json
import sys
import time
import urllib.error
import urllib.request
import uuid


def main(url, body_file, delivery_file=None):
    secret = sys.stdin.readline().strip().encode()
    if len(secret) < 16:
        sys.exit("the signing secret on stdin is shorter than 16 bytes")
    with open(body_file, "rb") as f:
        raw = f.read()
    timestamp = str(int(time.time()))
    event_id = str(uuid.uuid4())
    headers = {
        "Content-Type": "application/json",
        "X-Cypress-Event": "run.completed",
        "X-Cypress-Event-Id": event_id,
        "X-Cypress-Event-Version": "1",
        "X-Cypress-Request-Id": str(uuid.uuid4()),
        "X-Cypress-Timestamp": timestamp,
        "X-Cypress-Idempotency-Key": event_id,
        "X-Cypress-Signature": "sha256=" + hmac.new(secret, timestamp.encode() + b"." + raw,
                                                    hashlib.sha256).hexdigest(),
    }
    if delivery_file:
        with open(delivery_file, "w", encoding="utf-8") as f:
            json.dump({"headers": headers, "body": raw.decode()}, f)
    req = urllib.request.Request(url, data=raw, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            status, answer = r.status, r.read(2048).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        with e:
            status, answer = e.code, e.read(2048).decode("utf-8", "replace")
    print("HTTP %d %s" % (status, answer))
    return 0 if 200 <= status < 300 else 1


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        sys.exit(__doc__)
    sys.exit(main(*sys.argv[1:]))
