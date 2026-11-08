#!/usr/bin/env python3
"""push-layout.py LAYOUT_DIR REGISTRY REPO - push the one image in an OCI layout to a
registry, by its manifest's digest, and print that digest.

    scripts/push-layout.py .cache/models/granite-4.0-1b.layout 127.0.0.1:5001 models/granite-4.0-1b

The registry is the one scripts/models.sh starts beside the kind cluster:
plain HTTP, no account, reachable only from this machine. Blobs and the
manifest go up byte for byte, so the digest a pod names is the digest the
build made, and the build is reproducible (models/Dockerfile), so it is the
digest the flagship pins in its models/models.lock. The registry is asked for
the manifest back and its digest checked before this exits 0.

Standard library only; a blob is streamed from disk, never read whole.
"""
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

CHUNK = 1 << 20


def request(method, url, data=None, headers=None, length=None):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    if length is not None:
        req.add_header("Content-Length", str(length))
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        with e:
            return e.code, dict(e.headers), e.read()


def blob_path(layout, digest):
    algo, _, hexd = digest.partition(":")
    return os.path.join(layout, "blobs", algo, hexd)


def push_blob(base, repo, layout, digest):
    status, _, _ = request("HEAD", "%s/v2/%s/blobs/%s" % (base, repo, digest))
    if status == 200:
        return
    status, headers, body = request("POST", "%s/v2/%s/blobs/uploads/" % (base, repo), data=b"", length=0)
    if status != 202:
        sys.exit("starting the upload of %s: HTTP %d %s" % (digest, status, body[:200]))
    location = headers.get("Location") or headers.get("location")
    url = urllib.parse.urljoin(base + "/", location)
    url += ("&" if "?" in url else "?") + "digest=" + urllib.parse.quote(digest)
    path = blob_path(layout, digest)
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        status, _, body = request("PUT", url, data=f, length=size,
                                  headers={"Content-Type": "application/octet-stream"})
    if status != 201:
        sys.exit("uploading %s: HTTP %d %s" % (digest, status, body[:200]))


def main(layout, registry, repo):
    base = "http://" + registry
    index = json.load(open(os.path.join(layout, "index.json")))
    if len(index.get("manifests", [])) != 1:
        sys.exit("%s holds %d images; push one" % (layout, len(index.get("manifests", []))))
    desc = index["manifests"][0]
    digest, media = desc["digest"], desc["mediaType"]
    raw = open(blob_path(layout, digest), "rb").read()
    if "sha256:" + hashlib.sha256(raw).hexdigest() != digest:
        sys.exit("the layout's manifest does not hash to %s" % digest)
    manifest = json.loads(raw)
    for d in [manifest["config"]] + manifest.get("layers", []):
        push_blob(base, repo, layout, d["digest"])
    status, _, body = request("PUT", "%s/v2/%s/manifests/%s" % (base, repo, digest), data=raw, length=len(raw),
                              headers={"Content-Type": media})
    if status != 201:
        sys.exit("pushing the manifest: HTTP %d %s" % (status, body[:200]))
    status, headers, back = request("GET", "%s/v2/%s/manifests/%s" % (base, repo, digest),
                                    headers={"Accept": media})
    if status != 200 or "sha256:" + hashlib.sha256(back).hexdigest() != digest:
        sys.exit("the registry does not serve %s back as pushed (HTTP %d)" % (digest, status))
    print(digest)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
