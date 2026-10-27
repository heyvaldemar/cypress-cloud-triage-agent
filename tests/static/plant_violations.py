#!/usr/bin/env python3
"""Every static check, shown the violation it exists for, has to fail.

    tests/static/plant_violations.py

A check that cannot fail is not a check. Each line of tests/static/plants.tsv
names a file, a piece of it, its replacement, and the check in
scripts/validate.sh that must catch it: a tag where a digest was, a
misspelled field, an agent that does not compile. Each plant goes
into its own copy of the repository; the run fails if the named check stays
green, or if the text a plant breaks is no longer in the file (a stale plant
would prove nothing while looking like a pass).

Run scripts/validate.sh first: the untouched tree has to pass every check,
and its .cache/ is copied so the plants do not re-download schemas.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    plants = []
    with open(os.path.join(ROOT, "tests", "static", "plants.tsv"), encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            plants.append([json.loads(x) for x in line.rstrip("\n").split("\t")])
    missed = stale = 0
    for fname, old, new, check, what in plants:
        work = tempfile.mkdtemp(prefix="plant-")
        try:
            shutil.copytree(ROOT, work, dirs_exist_ok=True, symlinks=True,
                            ignore=shutil.ignore_patterns(".git", "out"))
            path = os.path.join(work, fname)
            text = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
            if old not in text:
                print("  STALE   %-12s %s: the text it breaks is no longer in %s" % (check, what, fname))
                stale += 1
                continue
            # An empty `old` plants a new file, in a new directory if need be.
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "w", encoding="utf-8").write(text.replace(old, new, 1))
            checks = [check]
            r = subprocess.run([os.path.join(work, "scripts", "validate.sh")] + checks,
                               capture_output=True, text=True, cwd=work)
            caught = "-- %s: FAILED" % check in r.stdout
            if caught:
                print("  caught  %-12s %s" % (check, what))
            else:
                print("  MISSED  %-12s %s" % (check, what))
                print("\n".join("          " + l for l in r.stdout.splitlines()[-15:]))
                missed += 1
        finally:
            shutil.rmtree(work, ignore_errors=True)
    print("%d plants: %d caught, %d missed, %d stale" % (len(plants), len(plants) - missed - stale, missed, stale))
    sys.exit(1 if (missed or stale) else 0)


if __name__ == "__main__":
    main()
