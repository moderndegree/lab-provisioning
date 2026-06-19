SHELL := /bin/bash

.PHONY: help \
        mini-provision mini-ping mini-syntax-check mini-lint mini-install-deps \
        ser5-init ser5-render ser5-provision ser5-ping ser5-syntax-check ser5-lint ser5-install-deps

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
