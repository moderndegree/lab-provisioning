SHELL       := /bin/bash
ANSIBLE_DIR := ansible
INVENTORY   := $(ANSIBLE_DIR)/inventory.ini
PLAYBOOK    := $(ANSIBLE_DIR)/site.yml
VAULT_FILE  := $(ANSIBLE_DIR)/group_vars/vault.yml
REQS_FILE   := $(ANSIBLE_DIR)/requirements.yml

.PHONY: provision ping syntax-check lint vault-edit install-deps help

## help: List available targets
help:
	@grep -E '^##' Makefile | sed 's/^## //'

## provision: Converge the host — idempotent, safe to re-run
provision:
	ansible-playbook -i $(INVENTORY) $(PLAYBOOK) --ask-vault-pass

## ping: Verify SSH connectivity to mini
ping:
	ansible -i $(INVENTORY) mini -m ping

## syntax-check: Validate playbook syntax (no vault required)
syntax-check:
	ansible-playbook --syntax-check -i $(INVENTORY) $(PLAYBOOK) \
	  -e @/dev/null --skip-tags never

## lint: Run ansible-lint and yamllint
lint:
	ansible-lint $(PLAYBOOK)
	yamllint $(ANSIBLE_DIR)

## vault-edit: Open vault.yml in your $EDITOR (decrypts/re-encrypts)
vault-edit:
	ansible-vault edit $(VAULT_FILE)

## vault-encrypt: Encrypt the plaintext vault stub (first-time setup)
vault-encrypt:
	ansible-vault encrypt $(VAULT_FILE)

## install-deps: Install required Ansible collections (run once)
install-deps:
	ansible-galaxy collection install -r $(REQS_FILE)
