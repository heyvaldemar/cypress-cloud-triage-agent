# The whole triage stack on kind, CPU only, at no cloud cost.
# A shell for every recipe: macOS make 3.81 runs a simple recipe without one,
# and the PATH exported below then never reaches it.
SHELL := /usr/bin/env bash
BIN := $(CURDIR)/.cache/bin
export PATH := $(BIN):$(PATH)

.PHONY: tools up test check down

tools:
	@./scripts/install-tools.sh >/dev/null

up: tools
	kind create cluster --config kind/cluster.yaml --wait 120s
	./scripts/install.sh

test: tools
	./tests/e2e/run.sh

check: tools
	./scripts/validate.sh
	python3 tests/static/plant_violations.py

down: tools
	kind delete cluster --name cypress-triage
