#!/usr/bin/env python3
"""The Jobs admission must refuse in triage-agents, each made from the real template.

    tests/e2e/plants.py   one JSON line per plant: {"name", "expect", "object"}

Every plant is the Job the receiver would create for a run (receiver.py's
job_for on receiver/job.json), with one thing changed: what a tricked or
replaced receiver might send. `expect` is the refusal message admission must
give, or "admitted" for the control, which is the receiver's Job unchanged.
tests/e2e/run.sh applies each with a server-side dry run.

Made from the template rather than written out, so a plant cannot drift from
what the receiver actually sends: a change to job.json changes every plant.
"""
import copy
import importlib.util
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
spec = importlib.util.spec_from_file_location("receiver", os.path.join(ROOT, "manifests/receiver/receiver.py"))
receiver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(receiver)
with open(os.path.join(ROOT, "manifests/receiver/job.json"), encoding="utf-8") as f:
    TEMPLATE = json.load(f)


def job():
    return receiver.job_for(TEMPLATE, ("kind01", 424242))


def pod(j):
    return j["spec"]["template"]["spec"]


def agent(j):
    return pod(j)["containers"][0]


def env(j, name, value):
    e = [x for x in agent(j)["env"] if x["name"] != name]
    agent(j)["env"] = e + [{"name": name, "value": value}]


def plant(name, expect, change):
    j = job()
    j["metadata"]["name"] = "plant-" + name
    change(j)
    print(json.dumps({"name": name, "expect": expect, "object": j}))


def as_deployment(j):
    d = {"apiVersion": "apps/v1", "kind": "Deployment",
         "metadata": {"name": j["metadata"]["name"], "namespace": "triage-agents"},
         "spec": {"replicas": 1, "selector": {"matchLabels": {"app.kubernetes.io/name": "triage-agent"}},
                  "template": copy.deepcopy(j["spec"]["template"])}}
    d["spec"]["template"]["spec"]["restartPolicy"] = "Always"
    j.clear()
    j.update(d)


plant("the-receivers-job", "admitted", lambda j: None)
plant("another-image", "runs the pinned agent image",
      lambda j: agent(j).update(image="docker.io/library/python:3.14-alpine"))
plant("another-command", "runs the triage agent's script",
      lambda j: agent(j).update(command=["python3", "-c", "import os; print(os.environ)"]))
plant("extra-arguments", "runs the triage agent's script", lambda j: agent(j).update(args=["--debug"]))
plant("a-proxy-in-the-environment", "the run and the doors' addresses",
      lambda j: env(j, "HTTPS_PROXY", "http://collector.outside.svc.cluster.local:8080"))
plant("a-secret-in-the-environment", "the run and the doors' addresses",
      lambda j: agent(j)["env"].append({"name": "RUN_NUMBER",
                                        "valueFrom": {"secretKeyRef": {"name": "slack-webhook", "key": "url"}}}))
plant("a-command-in-the-run-number", "RUN_NUMBER a run number", lambda j: env(j, "RUN_NUMBER", "7; wget x"))
plant("a-path-in-the-project", "RUN_PROJECT is a Cypress project ID", lambda j: env(j, "RUN_PROJECT", "../etc"))
plant("an-api-token", "gets no Kubernetes API token", lambda j: pod(j).update(automountServiceAccountToken=True))
plant("another-service-account", "runs as the triage-agent ServiceAccount",
      lambda j: pod(j).update(serviceAccountName="default"))
plant("a-token-for-the-api-server", "for one of its two doors",
      lambda j: pod(j)["volumes"][1]["projected"]["sources"][0]["serviceAccountToken"].update(
          audience="https://kubernetes.default.svc.cluster.local"))
plant("a-token-for-a-day", "expires within an hour",
      lambda j: pod(j)["volumes"][1]["projected"]["sources"][0]["serviceAccountToken"].update(expirationSeconds=86400))
plant("a-secret-volume", "no Secret or other volume",
      lambda j: pod(j)["volumes"].append({"name": "slack", "secret": {"secretName": "slack-webhook"}}))
plant("another-script", "no Secret or other volume",
      lambda j: pod(j)["volumes"][0]["configMap"].update(name="something-else"))
plant("a-gate-holding-a-token", "the gate before the agent mounts nothing",
      lambda j: pod(j)["initContainers"][0].update(
          volumeMounts=[{"name": "notify-token", "mountPath": "/var/run/secrets/notify"}]))
plant("the-clusters-resolver", "asks the agents' resolver",
      lambda j: pod(j).update(dnsPolicy="ClusterFirst", dnsConfig={"nameservers": ["10.96.0.10"]}))
plant("retries", "runs once", lambda j: j["spec"].update(backoffLimit=6))
plant("no-deadline", "runs once", lambda j: j["spec"].pop("activeDeadlineSeconds"))
plant("a-writable-root", "read-only root filesystem",
      lambda j: agent(j)["securityContext"].update(readOnlyRootFilesystem=False))
plant("no-limits", "declares CPU and memory limits", lambda j: agent(j)["resources"].pop("limits"))
plant("a-deployment", "only Jobs run in triage-agents", as_deployment)
