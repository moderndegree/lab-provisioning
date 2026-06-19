# lab-provisioning

Bare-metal IaC for the Minisforum MS-S1 Max (AMD Ryzen AI Max+ 395 "Strix Halo",
128 GB unified RAM, two NVMe slots). Provisions `mini` — a headless Ubuntu Server
24.04 LTS node — from a freshly wiped disk to a fully configured state.

**Primary GPU backend: AMD ROCm** (with RADV/Vulkan fallback via a single var flip).  
**Remote access: Tailscale** (all services reachable over the tailnet).

---

## Wipe → Reflash → Pull → Provision Loop

```
1.  Wipe the OS disk and boot the autoinstall USB
2.  Autoinstall runs unattended → SSH-reachable box (≈ 10 min)
3.  From your laptop:  git clone <REPO_URL>  &&  cd lab-provisioning
4.  Edit ansible/inventory.ini  →  set ansible_host= to mini's IP
5.  make install-deps            (once — installs Ansible collections)
6.  make vault-encrypt           (once — encrypt vault.yml after filling in secrets)
7.  make provision               (idempotent — safe to re-run at any time)
```

After the first provision: `make provision` is the single converge command. Re-run it
after any change to roles or vars to bring mini to the desired state.

---

## Repo Layout

```
lab-provisioning/
  README.md                         this file
  Makefile                          make provision | ping | syntax-check | lint
  .gitignore
  autoinstall/
    user-data                       cloud-init NoCloud — unattended OS install
    meta-data                       empty (required by NoCloud source)
  ansible/
    requirements.yml                Ansible collection dependencies
    inventory.ini                   single host: mini
    site.yml                        master playbook
    group_vars/
      all.yml                       pinned versions, Ollama env, gpu_backend, feature flags
      vault.yml                     ansible-vault encrypted secrets (NEVER commit plaintext)
    roles/
      base/                         kernel, packages, UFW, data disk mount
      amdgpu_rocm/                  ROCm stack + Vulkan fallback drivers
      ollama/                       Ollama LLM server + systemd override
      harness/                      Node.js, opencode-ai, Hermes Agent + gateway service
      tailscale/                    tailnet join
      cloudflared/                  Cloudflare Zero Trust tunnel stub (off by default)
```

---

## Prerequisites (on your provisioning machine)

- Ansible ≥ 2.15 — `pip install ansible`
- ansible-lint — `pip install ansible-lint`
- yamllint — `pip install yamllint`
- cloud-init CLI (for validating user-data) — `pip install cloud-init` or Ubuntu package

Install Ansible collections once:
```bash
make install-deps
```

---

## Autoinstall USB

1. Download Ubuntu Server 24.04 LTS ISO
2. Flash to USB (Balena Etcher, `dd`, or Ventoy)
3. Replace the ISO's `NoCloud` data source with the files in `autoinstall/`:
   - `autoinstall/user-data` → the autoinstall config
   - `autoinstall/meta-data` → empty file (required)

   The standard approach is to serve them via a second partition or a local HTTP
   server at boot. See: https://ubuntu.com/server/docs/install/autoinstall

4. **Before flashing**: fill in the two placeholders in `autoinstall/user-data`
   (SSH public key and password hash — see Placeholder Table below).

---

## Secrets Setup (vault.yml)

```bash
# 1. Edit ansible/group_vars/vault.yml and replace all PLACEHOLDER_* values
#    with your real secrets (see Placeholder Table below).

# 2. Encrypt the file with a strong passphrase:
make vault-encrypt

# 3. Commit the resulting ciphertext ($ANSIBLE_VAULT;1.1;AES256...).
#    The plaintext file must NEVER be committed.

# Future edits:
make vault-edit
```

---

## GPU Backend

Default: **ROCm** (`gpu_backend: rocm` in `ansible/group_vars/all.yml`).

If ROCm regresses on a kernel update, switch to the RADV/Vulkan backend
by changing one line and re-running `make provision` — no other changes needed:

```yaml
# ansible/group_vars/all.yml
gpu_backend: "vulkan"   # was: rocm
```

Mesa Vulkan drivers are always installed regardless of backend.

**Note on gfx1151 (Strix Halo):** The Radeon 8060S (gfx1151) is NOT on AMD's official
ROCm support matrix. The `HSA_OVERRIDE_GFX_VERSION=11.5.1` environment variable (set in
both `/etc/profile.d/rocm.sh` and the Ollama systemd override) causes the HSA runtime to
use the closest supported RDNA3 target. This is a community workaround — see TODO #2.

---

## Make Targets

| Target | Description |
|--------|-------------|
| `make provision` | Run the full playbook (converge) |
| `make ping` | Test SSH connectivity to mini |
| `make syntax-check` | Validate playbook syntax without connecting |
| `make lint` | Run ansible-lint + yamllint |
| `make vault-edit` | Decrypt, edit, and re-encrypt vault.yml |
| `make vault-encrypt` | Encrypt plaintext vault.yml for the first time |
| `make install-deps` | Install required Ansible collections (run once) |

---

## Optional: cloudflared (disabled by default)

Exposes services via Cloudflare Zero Trust without opening inbound ports.

To enable:
1. Add your tunnel token to `vault_cloudflared_token` in `vault.yml`
2. Set `enable_cloudflared: true` in `ansible/group_vars/all.yml`
3. Run `make provision`

---

## Placeholder Table

Every value that must be supplied before provisioning is listed here.
Search for `PLACEHOLDER_` in the repo to find each one.

| # | Placeholder | Where | How to generate |
|---|-------------|-------|-----------------|
| 1 | `PLACEHOLDER_SSH_PUBLIC_KEY` | `autoinstall/user-data` | `cat ~/.ssh/id_ed25519.pub` |
| 2 | `PLACEHOLDER_PASSWORD_HASH` | `autoinstall/user-data` + `vault.yml` | `python3 -c "import crypt; print(crypt.crypt('pw', crypt.mksalt(crypt.METHOD_SHA512)))"` |
| 3 | `PLACEHOLDER_TAILSCALE_AUTH_KEY` | `vault.yml` → `vault_tailscale_authkey` | https://login.tailscale.com/admin/settings/keys |
| 4 | `PLACEHOLDER_HERMES_API_KEY` | `vault.yml` → `vault_hermes_api_key` | Your LLM provider (OpenRouter, OpenAI, Nous Portal, etc.) |
| 5 | `PLACEHOLDER_CLOUDFLARED_TOKEN` | `vault.yml` → `vault_cloudflared_token` | Cloudflare Zero Trust dashboard (only needed if enabling cloudflared) |
| 6 | `PLACEHOLDER_MINI_IP` | `ansible/inventory.ini` | IP address or hostname of mini after autoinstall |
| 7 | `PLACEHOLDER_MINI_IP` | `ansible/inventory.ini` → `ansible_host` | Run `ip a` on mini after install |
| 8 | `data_disk_device` | `ansible/group_vars/all.yml` | Confirm with `lsblk -d` — default `/dev/nvme1n1` |
| 9 | `<REPO_URL>` | Makefile / README | Your git remote URL |

---

## TODOs (must resolve before first provision)

**TODO 1 — ROCm package set for gfx1151**  
The `amdgpu_rocm` role uses `amdgpu-install --usecase=rocm`. gfx1151 is not on
AMD's official support matrix. Before running, verify the correct `--usecase` flag
and any additional packages required for Strix Halo against a current community guide.
- ROCm compat matrix: https://rocm.docs.amd.com/en/latest/compatibility/compatibility-matrix.html
- Community discussion: search r/LocalLLaMA, AMD ROCm GitHub issues for "gfx1151" or "Strix Halo"

**TODO 2 — Minimum kernel version for gfx1151**  
`linux-generic-hwe-24.04` ships kernel 6.8.x. Community reports suggest mainline ≥ 6.11
may improve Strix Halo stability. If you need mainline, replace `linux-generic-hwe-24.04`
in `ansible/roles/base/tasks/main.yml` with the appropriate `linux-image-*` package from
the Ubuntu mainline PPA: https://kernel.ubuntu.com/~kernel-ppa/mainline/

**TODO 3 — Hermes Agent gateway dashboard port**  
`hermes_dashboard_port: 8080` is based on community reports. Verify the port by running
`hermes gateway start` on mini and checking the output, then update `group_vars/all.yml`.

**TODO 4 — Hermes Agent version pinning**  
The NousResearch installer always installs the latest published version. The documented
version (0.16.0 / v2026.6.5) is for reference. Pinned installation will be possible once
the installer supports a `--version` flag — track:
https://github.com/NousResearch/hermes-agent/releases

---

## Pinned Versions (as of 2026-06-19)

| Component | Version | Source |
|-----------|---------|--------|
| Ubuntu Server | 24.04 LTS | ubuntu.com |
| Kernel (baseline) | HWE (6.8.x) | linux-generic-hwe-24.04 |
| Node.js | LTS v22.x | NodeSource setup_lts.x |
| opencode-ai | 1.17.8 | npmjs.com |
| Hermes Agent | 0.16.0 (v2026.6.5) | NousResearch |
| amdgpu-install | 6.3.60303-1 | repo.radeon.com |
| Ollama | latest (official installer) | ollama.com |
| Tailscale | latest (official installer) | tailscale.com |

