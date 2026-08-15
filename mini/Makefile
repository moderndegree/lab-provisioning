SHELL       := /bin/bash
ANSIBLE_DIR := ansible
INVENTORY   := $(ANSIBLE_DIR)/inventory.ini
PLAYBOOK    := $(ANSIBLE_DIR)/site.yml
VERIFY_PLAYBOOK := $(ANSIBLE_DIR)/verify.yml
VAULT_FILE  := $(ANSIBLE_DIR)/group_vars/vault.yml
REQS_FILE   := $(ANSIBLE_DIR)/requirements.yml
BOOTSTRAP   := .bootstrap.env

# Variables that envsubst will substitute in template files.
# Listing them explicitly prevents accidental substitution of unrelated shell vars.
RENDER_VARS := $${USERNAME} $${USER_FULL_NAME} $${HOSTNAME} $${PASSWORD_HASH} \
               $${SSH_PUBLIC_KEY} $${OS_DISK} $${TIMEZONE} $${LOCALE} \
               $${KEYBOARD_LAYOUT} $${HOST_IP} $${WIFI_SSID} $${WIFI_PSK}

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

.PHONY: provision preview verify show-config ping syntax-check lint vault-edit vault-encrypt install-deps init render help

## help: List available targets
help:
	@grep -E '^##' Makefile | sed 's/^## //'

## init: Copy *.example files to their real (gitignored) counterparts
init:
	@test -f $(BOOTSTRAP)                              || cp .bootstrap.env.example $(BOOTSTRAP)
	@test -f ansible/inventory.ini                     || cp ansible/inventory.example.ini ansible/inventory.ini
	@test -f ansible/group_vars/all.yml                || cp ansible/group_vars/all.example.yml ansible/group_vars/all.yml
	@test -f ansible/group_vars/vault.yml              || cp ansible/group_vars/vault.yml.example ansible/group_vars/vault.yml
	@echo "==> Done. Fill in $(BOOTSTRAP) and ansible/group_vars/all.yml, then run: make render"

## render: Generate autoinstall/user-data and inventory.ini from .bootstrap.env
render:
	@test -f $(BOOTSTRAP) || { echo "ERROR: $(BOOTSTRAP) not found. Run: make init first"; exit 1; }
	@echo "==> Rendering templates from $(BOOTSTRAP)..."
	@set -a; source $(BOOTSTRAP); set +a; \
	  envsubst '$(RENDER_VARS)' < autoinstall/user-data.example > autoinstall/user-data; \
	  envsubst '$(RENDER_VARS)' < ansible/inventory.example.ini > ansible/inventory.ini; \
	  if [ -z "$${WIFI_SSID}" ]; then \
	    sed -i '/>>> WIFI /,/<<< WIFI <<</d' autoinstall/user-data; \
	    echo "==> No WIFI_SSID set — removed Wi-Fi block (wired ethernet only)."; \
	  else \
	    sed -i -e '/>>> WIFI/d' -e '/<<< WIFI/d' autoinstall/user-data; \
	    echo "==> Wi-Fi configured for SSID: $${WIFI_SSID}"; \
	  fi
	@echo "==> Generated: autoinstall/user-data  ansible/inventory.ini"
	@echo "    Validate:  cloud-init schema --config-file autoinstall/user-data"

## provision: Converge the host — idempotent, safe to re-run
provision:
	ansible-playbook -i $(INVENTORY) $(PLAYBOOK) $(VAULT_ARG) -e @$(VAULT_FILE)

## preview: Dry-run — show exactly what WOULD change, without applying anything
# The honest answer to "did my policy actually land?". Runs the real playbook in
# check mode with diffs, so the systemd override renders with the values Ansible
# would truly use (role defaults + your group_vars/all.yml overrides, correctly
# layered) and prints a line-by-line diff against what is on the box right now.
# Run this after every `git pull` — a stale override in all.yml shows up here as
# a value that refuses to change.
preview:
	ansible-playbook -i $(INVENTORY) $(PLAYBOOK) $(VAULT_ARG) -e @$(VAULT_FILE) --check --diff

## verify: Assert what mini is RUNNING matches what is committed (read-only)
# `preview` compares files on disk against what Ansible would write. It cannot
# see the gap between a correct file and a correct running system: a quadlet
# systemd never reloaded, a stale unit in ~/.config/systemd/user shadowing the
# generated one, a kernel cmdline that needs the reboot that never happened, an
# ollama that came back and is now fighting llama-server for 122 GiB.
#
# This asks the box — and the llama-servers themselves — what is actually true.
# It changes nothing, and it reports every finding at once rather than stopping
# at the first. Run it after every provision, and before believing any benchmark.
verify:
	ansible-playbook -i $(INVENTORY) $(VERIFY_PLAYBOOK) $(VAULT_ARG) -e @$(VAULT_FILE)

## show-config: Print the values Ansible will actually apply, after precedence
# Role defaults are the lowest-precedence vars, so a key copied into the
# gitignored group_vars/all.yml pins that value forever and silently shadows
# every later `git pull`. This is how you find out that happened.
# SECRETS ARE FILTERED HERE, NOT BY THE READER. This loads the decrypted vault
# (it has to — half the interesting values template off it), so dumping hostvars
# raw would print vault_restic_password, vault_tailscale_authkey,
# vault_cloudflared_token and every var that resolves from them, in plaintext, to
# a terminal and whatever is scrolling it back. The rejectattr below drops any key
# whose NAME looks secret, which also covers derived keys like restic_password
# and llamacpp_hf_token. Widen the pattern before adding a secret with a new name.
show-config:
	@ansible -i $(INVENTORY) mini $(VAULT_ARG_RO) -e @$(VAULT_FILE) \
	  -m ansible.builtin.debug -a "msg={{ hostvars[inventory_hostname] \
	    | dict2items \
	    | rejectattr('key', 'search', 'vault_|password|passwd|token|authkey|secret|psk|_key$$') \
	    | rejectattr('key', 'match', 'ansible_(env|facts|.*_interfaces)') \
	    | items2dict }}"

## ping: Verify SSH connectivity to mini
ping:
	ansible -i $(INVENTORY) mini -m ping

## syntax-check: Validate playbook syntax (uses .vault_pass if present)
syntax-check:
	ansible-playbook --syntax-check -i $(INVENTORY) $(PLAYBOOK) $(VERIFY_PLAYBOOK) $(VAULT_ARG_RO) \
	  --skip-tags never

## lint: Run ansible-lint and yamllint
lint:
	ansible-lint $(PLAYBOOK) $(VERIFY_PLAYBOOK)
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
