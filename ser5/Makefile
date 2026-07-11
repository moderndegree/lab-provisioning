SHELL       := /bin/bash
ANSIBLE_DIR := ansible
INVENTORY   := $(ANSIBLE_DIR)/inventory.ini
PLAYBOOK    := $(ANSIBLE_DIR)/site.yml
VAULT_FILE  := $(ANSIBLE_DIR)/group_vars/vault.yml
REQS_FILE   := $(ANSIBLE_DIR)/requirements.yml
BOOTSTRAP   := .bootstrap.env

# Variables that envsubst will substitute in template files.
# Listing them explicitly prevents accidental substitution of unrelated shell vars.
RENDER_VARS := $${USERNAME} $${USER_FULL_NAME} $${HOSTNAME} $${PASSWORD_HASH} \
               $${SSH_PUBLIC_KEY} $${OS_DISK} $${TIMEZONE} $${LOCALE} \
			   $${KEYBOARD_LAYOUT} $${HOST_IP} $${WIFI_SSID} $${WIFI_PSK}

# Vault auth: use .vault_pass file if present; otherwise prompt interactively.
VAULT_PASS_FILE := $(wildcard .vault_pass)
ifeq ($(VAULT_PASS_FILE),)
  VAULT_ARG    := --ask-vault-pass
  VAULT_ARG_RO :=
else
  VAULT_ARG    := --vault-password-file $(VAULT_PASS_FILE)
  VAULT_ARG_RO := --vault-password-file $(VAULT_PASS_FILE)
endif

.PHONY: init render provision preflight ping syntax-check lint \
        vault-edit vault-encrypt install-deps help

## help           List available make targets
help:
	@grep -E '^## ' Makefile | sed 's/^## //'

## init           Copy *.example files to their real (gitignored) counterparts
init:
	@echo "==> Initialising local config files..."
	@test -f $(BOOTSTRAP)                                || cp .bootstrap.env.example $(BOOTSTRAP)
	@test -f ansible/group_vars/all.yml                  || cp ansible/group_vars/all.example.yml ansible/group_vars/all.yml
	@test -f ansible/group_vars/vault.yml                || cp ansible/group_vars/vault.yml.example ansible/group_vars/vault.yml
	@test -f ansible/inventory.ini                       || cp ansible/inventory.example.ini ansible/inventory.ini
	@echo "==> Done. Fill in these files, then run: make render"
	@echo "    $(BOOTSTRAP)                  — USERNAME, HOSTNAME, SSH key, IP, disk paths"
	@echo "    ansible/group_vars/all.yml    — same identity + Ansible-specific vars"
	@echo "    ansible/group_vars/vault.yml  — secrets (then: make vault-encrypt)"

## render         Generate autoinstall/user-data and inventory.ini from .bootstrap.env
render:
	@test -f $(BOOTSTRAP) || { echo "ERROR: $(BOOTSTRAP) not found. Run: make init first"; exit 1; }
	@echo "==> Rendering templates from $(BOOTSTRAP)..."
	@set -a; source $(BOOTSTRAP); set +a; \
	  envsubst '$(RENDER_VARS)' < autoinstall/user-data.example > autoinstall/user-data; \
	  envsubst '$(RENDER_VARS)' < ansible/inventory.example.ini > ansible/inventory.ini; \
	  if [ -z "$${WIFI_SSID}" ]; then \
	    sed -i -e '/>>> WIFI /,/<<< WIFI <<</d' -e '/>>> WIFI-LATE /,/<<< WIFI-LATE <<</d' autoinstall/user-data; \
	    echo "==> No WIFI_SSID set — removed Wi-Fi blocks (install will use wired ethernet)."; \
	  else \
	    sed -i -e '/>>> WIFI/d' -e '/<<< WIFI/d' autoinstall/user-data; \
	    echo "==> Wi-Fi configured for SSID: $${WIFI_SSID}"; \
	  fi
	@echo "==> Generated:"
	@echo "    autoinstall/user-data   (flash to USB for autoinstall)"
	@echo "    ansible/inventory.ini   (Ansible target host)"
	@echo ""
	@echo "    Validate autoinstall config:"
	@echo "    cloud-init schema --config-file autoinstall/user-data"

## preflight      Check that config placeholders have been filled in
preflight:
	@echo "==> Preflight: scanning for unfilled placeholders..."
	@bad=0; \
	if grep -qE 'YOUR_USERNAME|YOUR_HOSTNAME|Your Full Name|your_comment_here|YOUR_GH_USERNAME' ansible/group_vars/all.yml; then \
	  echo "ERROR: placeholders remain in ansible/group_vars/all.yml"; bad=1; fi; \
	if head -n1 $(VAULT_FILE) | grep -q '\$$ANSIBLE_VAULT'; then \
	  echo "    vault.yml is encrypted — skipping secret placeholder scan"; \
	else \
	  if grep -qE 'PLACEHOLDER_SHA512_CRYPT_HASH|tskey-auth-PLACEHOLDER|PLACEHOLDER_WIFI_PASSWORD' $(VAULT_FILE); then \
	    echo "ERROR: placeholders remain in $(VAULT_FILE) (run: make vault-edit)"; bad=1; fi; \
	fi; \
	if [ "$$bad" -ne 0 ]; then echo "==> Fill the values above, then re-run."; exit 1; fi; \
	echo "==> Preflight passed."

## provision      Converge ser5 end-to-end — idempotent, safe to re-run
provision: preflight
	ansible-playbook -i $(INVENTORY) $(PLAYBOOK) $(VAULT_ARG) -e @$(VAULT_FILE)

## ping           Verify SSH connectivity to ser5
ping:
	ansible -i $(INVENTORY) ser5_host -m ping

## syntax-check   Validate playbook syntax (uses .vault_pass if present)
syntax-check:
	ansible-playbook --syntax-check -i $(INVENTORY) $(PLAYBOOK) $(VAULT_ARG_RO) \
	  -e @/dev/null

## lint           Run ansible-lint and yamllint over the playbook
lint:
	ansible-lint $(PLAYBOOK)
	yamllint $(ANSIBLE_DIR)

## vault-edit     Open vault.yml in $$EDITOR (decrypts/re-encrypts in-place)
vault-edit:
	ansible-vault edit $(VAULT_FILE)

## vault-encrypt  Encrypt the plaintext vault.yml stub (run once after make init)
vault-encrypt:
	ansible-vault encrypt $(VAULT_FILE)

## install-deps   Install required Ansible collections from requirements.yml
install-deps:
	ansible-galaxy collection install -r $(REQS_FILE)
