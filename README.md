# lab-provisioning

Bare-metal IaC for the Minisforum MS-S1 Max (AMD Ryzen AI Max+ 395 "Strix Halo",
128 GB unified RAM, two NVMe slots). Provisions `mini` — a headless Ubuntu Server
26.04 LTS node (kernel 7.0) — from a freshly wiped disk to a fully configured state.

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
      base/                         kernel cmdline (GRUB), firmware pin, packages, UFW, data disk
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

## BIOS — one-time prerequisites (survives an SSD wipe)

These are set in firmware, not by Ansible, so they persist across reflashes. Do them
once before the first autoinstall:

1. **Update to the latest BIOS first** (fixes idle noise + DPC latency).
2. Integrated Graphics / **UMA Frame Buffer Size → 512MB**.
3. **Disable IOMMU.** (Ansible also passes `amd_iommu=off` on the kernel cmdline as
   belt-and-suspenders; this kills VFIO passthrough, which is fine for headless inference.)
4. **Power mode: Performance** (130W sustained / 160W peak) or **Rack** (140W sustained).
   A headless box should take the throughput and ignore fan noise.

---

## Autoinstall USB

1. Download Ubuntu Server 26.04 LTS ISO
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
both `/etc/profile.d/rocm.sh` and the Ollama systemd override) makes the HSA runtime
recognise the GPU as the nearest supported RDNA3 target. ROCm is installed **userspace
only** (`amdgpu-install --no-dkms`); the in-tree `amdgpu` driver that ships with kernel
7.0 drives the GPU. This is a community-proven workaround — re-run the verify block
(below) after any kernel or ROCm bump.

---

## Verify after boot

After the first provision (and after any kernel or ROCm bump), confirm the GPU stack
on mini before trusting it:

```bash
cat /proc/cmdline                 # confirm amd_iommu=off + GTT/ttm flags applied
rocminfo | grep -i gfx1151        # ROCm sees the GPU
dmesg | grep -i gtt               # GTT sizing
id ollama                         # service account has render + video groups
journalctl -u ollama | grep -i rocm
#   expect: library=ROCm compute=gfx1151 ... total ~111 GiB available ~110 GiB
```

Expected performance (Q4-class, gfx1151): **~40 tok/s on a 30B model** via Ollama + ROCm.
If you ever move off Ollama to a current `llama-server` build, standalone llama.cpp on
Vulkan/RADV is the ceiling (~98–103 tok/s on Qwen3-30B; ~170 tok/s on small MoE).

---

## Watch-outs

- **No ROCm nightlies (7.9–7.12).** They cap memory allocation at 64 GB — useless on
  this 128 GB box. Stay on the pinned 7.2.x production stream.
- **Never install `linux-firmware-20251125`** — it breaks ROCm on Strix Halo. The `base`
  role pins that build out via `/etc/apt/preferences.d/no-bad-firmware`.
- **Leave RAM headroom.** Very large context (e.g. 200k on a 30B) can OOM and crash the
  whole box on unified memory.
- **Re-verify after any bump.** Re-run the verify block above after any kernel or ROCm
  upgrade before trusting the node.
- **gfx1151 is community-supported only.** Pin versions; nothing here is on AMD's
  official ROCm support matrix.

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
| 2 | `PLACEHOLDER_PASSWORD_HASH` | `autoinstall/user-data` + `vault.yml` | `mkpasswd --method=SHA-512` (from the `whois` package) |
| 3 | `PLACEHOLDER_TAILSCALE_AUTH_KEY` | `vault.yml` → `vault_tailscale_authkey` | https://login.tailscale.com/admin/settings/keys |
| 4 | `PLACEHOLDER_HERMES_API_KEY` | `vault.yml` → `vault_hermes_api_key` | Your LLM provider (OpenRouter, OpenAI, Nous Portal, etc.) |
| 5 | `PLACEHOLDER_CLOUDFLARED_TOKEN` | `vault.yml` → `vault_cloudflared_token` | Cloudflare Zero Trust dashboard (only needed if enabling cloudflared) |
| 6 | `PLACEHOLDER_MINI_IP` | `ansible/inventory.ini` | IP address or hostname of mini after autoinstall |
| 7 | `PLACEHOLDER_MINI_IP` | `ansible/inventory.ini` → `ansible_host` | Run `ip a` on mini after install |
| 8 | `data_disk_device` | `ansible/group_vars/all.yml` | Confirm with `lsblk -d` — default `/dev/nvme1n1` |
| 9 | `<REPO_URL>` | Makefile / README | Your git remote URL |

---

## TODOs (must resolve before first provision)

**TODO 1 — ROCm package set for gfx1151 — RESOLVED**
The `amdgpu_rocm` role installs ROCm 7.2.4 production userspace with
`amdgpu-install -y --usecase=rocm --no-dkms`. DKMS is skipped because it fails to
build on kernel 7.0 and the APU uses the in-tree `amdgpu` driver. Do **not** use
ROCm 7 nightlies — they cap memory allocation at 64 GB (useless on 128 GB).
NOTE: AMD has not published a 26.04 ROCm repo; the role intentionally pulls the
`noble` (24.04) `amdgpu-install` deb — the userspace it installs runs on 26.04.
Change the codename in `roles/amdgpu_rocm/defaults/main.yml` if AMD ships a 26.04 repo.

**TODO 2 — Minimum kernel version for gfx1151 — RESOLVED**
The gfx1151 stability floor is kernel **>= 6.18.4**. Ubuntu Server 26.04 LTS ships
kernel 7.0, which clears it with no HWE or mainline-PPA juggling — so the `base` role
installs no extra kernel package. **Do NOT install `linux-firmware-20251125`** — it
breaks ROCm on Strix Halo; the `base` role pins that build out via apt preferences.

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
| Ubuntu Server | 26.04 LTS | ubuntu.com |
| Kernel (baseline) | 7.0 (GA) | 26.04 default — clears gfx1151 >= 6.18.4 floor |
| Node.js | LTS v22.x | NodeSource setup_lts.x |
| opencode-ai | 1.17.8 | npmjs.com |
| Hermes Agent | 0.16.0 (v2026.6.5) | NousResearch |
| amdgpu-install | 7.2.4.70204-1 (ROCm 7.2.4) | repo.radeon.com (noble) |
| Ollama | latest (official installer) | ollama.com |
| Tailscale | latest (official installer) | tailscale.com |

