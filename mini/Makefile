SHELL       := /bin/bash
ANSIBLE_DIR := ansible
INVENTORY   := $(ANSIBLE_DIR)/inventory.ini
PLAYBOOK    := $(ANSIBLE_DIR)/site.yml
VAULT_FILE  := $(ANSIBLE_DIR)/group_vars/vault.yml
REQS_FILE   := $(ANSIBLE_DIR)/requirements.yml

# Vault auth: if a gitignored .vault_pass file exists, use it automatically
# (no prompts — and lets `make syntax-check`/`lint` load the encrypted vault).
# Otherwise fall back to prompting interactively on the targets that need it.
VAULT_PASS_FILE := $(wildcard .vault_pass)
ifeq ($(VAULT_PASS_FILE),)
  VAULT_ARG    := --ask-vault-pass
  VAULT_ARG_RO :=
else
  VAULT_ARG    := --vault-password-file $(VAULT_PASS_FILE)
  VAULT_ARG_RO := --vault-password-file $(VAULT_PASS_FILE)
endif

.PHONY: provision ping syntax-check lint vault-edit vault-encrypt install-deps help

## help: List available targets
help:
	@grep -E '^##' Makefile | sed 's/^## //'

## provision: Converge the host — idempotent, safe to re-run
provision:
	ansible-playbook -i $(INVENTORY) $(PLAYBOOK) $(VAULT_ARG) -e @$(VAULT_FILE)

## ping: Verify SSH connectivity to mini
ping:
	ansible -i $(INVENTORY) mini -m ping

## syntax-check: Validate playbook syntax (uses .vault_pass if present)
syntax-check:
	ansible-playbook --syntax-check -i $(INVENTORY) $(PLAYBOOK) $(VAULT_ARG_RO) \
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
