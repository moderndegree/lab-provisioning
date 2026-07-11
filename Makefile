SHELL := /bin/bash

.PHONY: help \
        mini-provision mini-ping mini-syntax-check mini-lint mini-install-deps \
        ser5-init ser5-render ser5-provision ser5-ping ser5-syntax-check ser5-lint ser5-install-deps \
        loopkit-venv loopkit-test loopkit-matrix loopkit-summary

## help               Show available targets
help:
	@echo "lab-provisioning — IaC for mini (inference) and ser5 (workstation)"
	@echo ""
	@echo "  Mini targets:"
	@echo "    mini-provision       Converge mini end-to-end"
	@echo "    mini-ping            SSH connectivity check"
	@echo "    mini-syntax-check    Validate mini playbook syntax"
	@echo "    mini-lint            ansible-lint + yamllint for mini"
	@echo "    mini-install-deps    Install mini Ansible collections"
	@echo ""
	@echo "  Ser5 targets:"
	@echo "    ser5-init            Copy *.example files to real (gitignored) counterparts"
	@echo "    ser5-render          Generate autoinstall/user-data and inventory.ini"
	@echo "    ser5-provision       Converge ser5 end-to-end"
	@echo "    ser5-ping            SSH connectivity check"
	@echo "    ser5-syntax-check    Validate ser5 playbook syntax"
	@echo "    ser5-lint            ansible-lint + yamllint for ser5"
	@echo "    ser5-install-deps    Install ser5 Ansible collections"
	@echo ""
	@echo "  Loopkit (packages/loopkit) targets:"
	@echo "    loopkit-venv         Create .venv and install loopkit (editable, with dev deps)"
	@echo "    loopkit-test         Run the loopkit unit tests"
	@echo "    loopkit-matrix       Run the Phase 1 baseline matrix (needs mini reachable)"
	@echo "    loopkit-summary      Generate the one-page quality summary from runs.db"
	@echo ""
	@echo "  Most operations are best run from the machine directory directly:"
	@echo "    cd mini && make provision"
	@echo "    cd ser5 && make init"

mini-provision:
	$(MAKE) -C mini provision

mini-ping:
	$(MAKE) -C mini ping

mini-syntax-check:
	$(MAKE) -C mini syntax-check

mini-lint:
	$(MAKE) -C mini lint

mini-install-deps:
	$(MAKE) -C mini install-deps

ser5-init:
	$(MAKE) -C ser5 init

ser5-render:
	$(MAKE) -C ser5 render

ser5-provision:
	$(MAKE) -C ser5 provision

ser5-ping:
	$(MAKE) -C ser5 ping

ser5-syntax-check:
	$(MAKE) -C ser5 syntax-check

ser5-lint:
	$(MAKE) -C ser5 lint

ser5-install-deps:
	$(MAKE) -C ser5 install-deps

# Uses uv when available (https://docs.astral.sh/uv/), plain venv+pip otherwise.
loopkit-venv:
	@if command -v uv >/dev/null 2>&1; then \
	  test -d .venv || uv venv -q .venv; \
	  uv pip install -q -p .venv/bin/python -e "packages/loopkit[dev]"; \
	else \
	  test -d .venv || python3 -m venv .venv; \
	  .venv/bin/pip install -q -e "packages/loopkit[dev]"; \
	fi
	@echo "==> .venv ready — run: .venv/bin/loopkit --help"

loopkit-test: loopkit-venv
	.venv/bin/pytest packages/loopkit/tests -q

loopkit-matrix: loopkit-venv
	LOOPKIT_BIN=$(CURDIR)/.venv/bin/loopkit packages/loopkit/suites/run-matrix.sh

loopkit-summary: loopkit-venv
	.venv/bin/loopkit summary
