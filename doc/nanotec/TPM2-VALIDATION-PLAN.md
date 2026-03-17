# TPM 2.0 Validation Plan — BCM2712 / Pi 5

Systematic validation of all TPM 2.0 features needed for the secure boot
chain, using the current stock test image (SoftSPI overlay, SLB9672 FW16).

Goal: Understand the full TPM lifecycle (provisioning, boot-time usage,
runtime operations, maintenance) before integrating into Ubuntu Core.

## Current Test Setup

- Pi 5 with Debian, U-Boot + SoftSPI + TPM2 TIS SPI
- Infineon SLB9672 FW 16.24 on LetsTrust eval board (CS1/GPIO 7)
- DT overlay: `soft-spi-tpm.dtbo` via config.txt
- U-Boot: `tpm2 startup`, `self_test`, `pcr_read` confirmed working
- Linux: `/dev/tpm0` present, `tpm2-tools` installed

## Design Decisions (Resolved)

### Hierarchy: Owner (not Platform)

platformAuth gets reset by firmware on every `TPM2_SU_CLEAR` startup.
Pi firmware behavior unverified. Owner hierarchy persists across reboots
(only cleared by `TPM2_Clear`), simpler auth model.

### NV Counter Authorization: ownerAuth password, no PCR binding

PCR values change with every firmware/kernel update — the policy would need
recalculation and NV index re-creation after each update. PCR policies are
counterproductive for counters that must be incremented during updates.

- Counter read: **no ownerAuth needed** — `authread` allows unauthenticated read (U-Boot)
- Counter increment: requires ownerAuth (Linux only, after successful OTA via `post-refresh` hook)
- ownerAuth storage: **NOT in U-Boot env** (Security Audit F2). Linux only: root-only file `/var/lib/tpm/owner-auth` or runtime derivation
- ownerAuth derivation: `HMAC-SHA256(master-secret, serial || EK_pubkey_hash)` (Security Audit F9)

### Phase 4 (FIT Key in TPM): Deferred

FIT signing public key is embedded in DTB via `mkimage -K`. DTB is inside
boot.img, verified by Pi firmware via boot.sig (RSA against OTP-fused key).
Trust chain: OTP → boot.sig → boot.img → DTB → FIT public key.
Storing same key in TPM adds no security gain. Commands preserved below as
educational reference only.

### TPM Failure Mode: Configurable

`tpm_required=0` (development: graceful degradation) or `tpm_required=1`
(production: hard fail). Compiled into U-Boot env inside signed boot.img.

### Counter Comparison: >= for validation, > for production

`image_version >= counter` allows reinstall of same version (useful for tests).
Production should use strict `>` to block replay of current version (F8).

---

## Phase 1: NV Counters (Anti-Rollback)

Two monotonic counters:
- **Counter 1 (0x01500001):** Kernel FIT image version
- **Counter 2 (0x01500002):** boot.img (Gadget) version

### 1.1 Define NV Counter Indices

```bash
# Counter 1: Kernel FIT rollback counter — owner hierarchy, counter type
tpm2_nvdefine 0x01500001 \
  --hierarchy=o \
  --size=8 \
  --attributes="nt=1|ownerread|ownerwrite|authread|authwrite" \
  -p <owner-auth-password>

# Counter 2: boot.img rollback counter
tpm2_nvdefine 0x01500002 \
  --hierarchy=o \
  --size=8 \
  --attributes="nt=1|ownerread|ownerwrite|authread|authwrite" \
  -p <owner-auth-password>
```

Note: For counter-type NV (`nt=1`), `ownerwrite`/`authwrite` enables
`tpm2_nvincrement` — TPM enforces that counter NV can only be incremented,
never written with arbitrary values.

### 1.2 Test NV Counter Operations (Linux)

```bash
# Read initial value (should be 0)
tpm2_nvread 0x01500001 --hierarchy=o | xxd

# Increment (requires ownerAuth)
tpm2_nvincrement 0x01500001 -P <owner-auth-password>

# Read again (should be 1) — no auth needed (authread)
tpm2_nvread 0x01500001 --hierarchy=o | xxd

# Increment multiple times, verify monotonicity
tpm2_nvincrement 0x01500001 -P <owner-auth-password>
tpm2_nvincrement 0x01500001 -P <owner-auth-password>
tpm2_nvread 0x01500001 --hierarchy=o | xxd
# Expected: 3
```

### 1.3 Test NV Counter from U-Boot

**BLOCKED: U-Boot has NO NV CLI commands.** Upstream master confirmed
(March 2026). Library functions exist in `lib/tpm-v2.c` but no CLI wrappers.

**Required patch (~50-80 lines, 3 files):**
1. `cmd/tpm-v2.c` — Add `do_tpm_nv_read`, `do_tpm_nv_write` (pattern: `cmd/tpm-v1.c:312-378`)
2. `include/tpm-v2.h` — Add `TPM2_CC_NV_INCREMENT = 0x0134`, new prototypes
3. `lib/tpm-v2.c` — Implement `tpm2_nv_increment()`, fix hierarchy in
   `tpm2_nv_read_value()` (hardcoded `TPM2_RH_PLATFORM` → accept parameter or default `TPM2_RH_OWNER`)

**After patch, verify:**
```
tpm2 startup TPM2_SU_CLEAR
tpm2 nv_read 0x01500001 0x02000000 8
md.l 0x02000000 2
```

Note: U-Boot needs `nv_read` only. `nv_increment` from U-Boot is optional —
Linux handles increment after successful OTA.

### 1.4 Counter Increment Strategy — snapd `post-refresh` Hook

Both kernel and gadget snap counters are incremented via snapd-native
`post-refresh` hooks. No custom boot detection — UC's existing mechanisms
are sufficient.

#### Anti-Rollback Check Flow (U-Boot, read-only)

```
1. U-Boot reads NV counter from TPM → current = M
2. U-Boot reads expected version from image → expected = N
3. If N < M → rollback detected → refuse boot
4. If N >= M → boot proceeds
5. After successful boot, increment counter to N (from Linux via post-refresh)
```

#### Kernel Snap Counter (NV Index 0x01500001)

Timing analysis confirms `post-refresh` is safe:
1. Kernel snap refresh → snapd sets `kernel_status=try` → Reboot
2. U-Boot boots "try" kernel, checks TPM counter (read-only)
3. Boot succeeds → snapd starts → `ensureBootOk()` runs FIRST in `DeviceManager.Ensure()`
4. `boot.MarkBootSuccessful()` called → `kernel_status=""` → boot confirmed
5. THEN tasks run: auto-connect → aliases → **post-refresh hook**
6. Hook increments NV counter

If boot FAILS: snapd reverts to old kernel, `post-refresh` never runs →
counter unchanged. Correct behavior.

#### Gadget Snap Counter (NV Index 0x01500002)

Gadget has NO A/B, but `post-refresh` is still safe:
1. Gadget refresh → `update-gadget-assets` writes new boot.img directly
2. Immediate reboot (`FinishTaskWithRestart → RestartSystem`)
3. Device boots with new U-Boot
4. snapd starts → `auto-connect` runs (first post-reboot task)
5. **`post-refresh` hook runs** → increment counter

Boot verification is implicit: if snapd runs and executes the hook,
the device HAS booted. If device doesn't boot → hook never runs →
counter unchanged. Manual recovery (flash old image) works as long as
counter hasn't been incremented.

Note: Gadget update without A/B = general UC risk (independent of counter).
boot.img is additionally protected by boot.sig + OTP (firmware level).

#### Common Flow (Both Snap Types)

```
1. snapd downloads new snap (kernel or gadget)
2. [Kernel: try-slot + kernel_status=try] [Gadget: direct overwrite]
3. Reboot
4. U-Boot checks TPM counter (read-only): current_counter vs image version
5. Version < counter → rollback attack → boot refused
6. Version >= counter → boot OK
7. Linux boots → snapd starts
8. [Kernel: ensureBootOk → MarkBootSuccessful] [Gadget: no A/B check]
9. post-refresh hook runs → increment counter
```

#### Hook Implementation

```bash
# meta/hooks/post-refresh (shell script — in respective snap)
#!/bin/sh
set -e

# Which counter? Kernel=0x01500001, Gadget=0x01500002
SNAP_TYPE=$(snapctl get snap-type)  # or hardcoded per snap
case "$SNAP_TYPE" in
    kernel) NV_INDEX="0x01500001" ;;
    gadget) NV_INDEX="0x01500002" ;;
    *) exit 0 ;;
esac

# Expected version from snap configuration
EXPECTED_VERSION=$(snapctl get tpm-counter-version)
if [ -z "$EXPECTED_VERSION" ]; then
    exit 0  # No counter management configured
fi

# Read current TPM counter (no auth needed — authread)
CURRENT=$(tpm2_nvread "$NV_INDEX" --hierarchy=o | xxd -p)
CURRENT_DEC=$((16#$CURRENT))

# Increment until target value reached
while [ "$CURRENT_DEC" -lt "$EXPECTED_VERSION" ]; do
    tpm2_nvincrement "$NV_INDEX" -P "$(cat /var/lib/tpm/owner-auth)"
    CURRENT_DEC=$((CURRENT_DEC + 1))
done

logger "TPM NV counter $NV_INDEX incremented to $EXPECTED_VERSION"
```

**Interface requirement:** Kernel + gadget snap need `tpm` interface plug
for `/dev/tpm*` and `/dev/tpmrm*` access. For validation: `devmode` or
`--dangerous` installation bypasses confinement.

---

## Phase 2: PCR Policy and Measured Boot

### 2.1 Understand Current PCR State

```bash
# Read all SHA-256 PCRs
tpm2_pcrread sha256

# Identify which PCRs the Pi firmware extends
# PCR 0: firmware measurements (already confirmed non-zero)
# Document which PCRs are used and which are free
```

**PCR Measurement Sequence (to be verified on hardware, F7):**
| PCR | Content | Extended by |
|-----|---------|-------------|
| 0 | Pi Firmware | Firmware |
| 4 | U-Boot binary | Firmware (when loading) |
| 9 | Kernel commandline (optional) | U-Boot (if implemented) |
| 11 | FIT image hash (optional) | U-Boot (after verify) |
| 16 | Debug/test (resettable) | Any stage |

Check: Event-Log via DT (`/chosen/linux,tpm-event-log`) — does SLB9672/Pi
firmware provide it?

### 2.2 Extend PCR from U-Boot

```
# Hash a known string to memory
hash sha256 *0x02000000 4 0x02000100
# Extend PCR 16 (debug/test PCR, resettable)
tpm2 pcr_extend 16 0x02000100
# Read back
tpm2 pcr_read 16 0x02000200
md.b 0x02000200 0x20
```

### 2.3 Verify PCR Continuity Across Boot Stages

```bash
# After booting into Linux, read PCR 16
tpm2_pcrread sha256:16
# Should match the extended value from U-Boot (cumulative hash)
```

### 2.4 Create a PCR Policy

```bash
# Create a policy that requires PCR 0 to have a specific value
tpm2_pcrread sha256:0 -o pcr0.bin
tpm2_startauthsession -S session.ctx
tpm2_policypcr -S session.ctx -l sha256:0 -f pcr0.bin -L policy.digest
tpm2_flushcontext session.ctx

# This policy.digest can be used for seal/unseal
```

**Recommended PCR policy for seal/unseal:** `sha256:0,4` (Firmware + U-Boot).
Not too many PCRs — each firmware/U-Boot update changes values and breaks seal.
bootargs are compiled in U-Boot env (signed boot.img), config.txt is inside
boot.img — no need to measure them separately (F3).

---

## Phase 3: Seal / Unseal

### 3.1 Basic Seal/Unseal (No Policy)

```bash
# Create a primary key in the owner hierarchy
tpm2_createprimary -C o -c primary.ctx

# Create a sealing object with a secret
echo "test-secret-data" > secret.txt
tpm2_create -C primary.ctx \
  -i secret.txt \
  -u seal.pub -r seal.priv

# Load the sealed object
tpm2_load -C primary.ctx \
  -u seal.pub -r seal.priv \
  -c seal.ctx

# Unseal
tpm2_unseal -c seal.ctx
# Should print: test-secret-data
```

### 3.2 Seal with PCR Policy

Use PCR 16 (resettable) for testing instead of PCR 0:

```bash
# Read current PCR 16 value
tpm2_pcrread sha256:16 -o pcr16.bin

# Create PCR 16 policy
tpm2_startauthsession -S session.ctx
tpm2_policypcr -S session.ctx -l sha256:16 -f pcr16.bin -L policy16.digest
tpm2_flushcontext session.ctx

# Seal with PCR 16 policy
tpm2_create -C primary.ctx \
  -i secret.txt \
  -L policy16.digest \
  -u seal_pcr.pub -r seal_pcr.priv

tpm2_load -C primary.ctx \
  -u seal_pcr.pub -r seal_pcr.priv \
  -c seal_pcr.ctx

# Unseal with policy session — should succeed
tpm2_startauthsession --policy-session -S session.ctx
tpm2_policypcr -S session.ctx -l sha256:16
tpm2_unseal -c seal_pcr.ctx -p session:session.ctx
tpm2_flushcontext session.ctx
```

### 3.3 Verify Seal Fails After PCR Change

```bash
# Extend PCR 16 to simulate different boot state
tpm2_pcrextend 16:sha256=0000000000000000000000000000000000000000000000000000000000000001

# Try unseal — should FAIL (PCR 16 changed)
tpm2_startauthsession --policy-session -S session.ctx
tpm2_policypcr -S session.ctx -l sha256:16
tpm2_unseal -c seal_pcr.ctx -p session:session.ctx
# Expected: TPM2_RC_POLICY_FAIL

# Reboot to reset PCR 16 → unseal should succeed again
```

### 3.4 Seal with PolicyAuthorize (Production Flow)

PolicyAuthorize solves the update problem: instead of binding directly to PCR
values (which change on every update), a signed policy is used. CI/CD
pre-signs policies with known PCR values from tested builds.

**Policy signing key lives in existing build HSM** (same as boot.img + FIT
signing keys). PCR values are predictable from deterministic builds (F4).

```bash
# === PROVISIONING ===

# 1. Primary key
tpm2_createprimary -C o -c primary.ctx

# 2. PolicyAuthorize setup
#    Authorization key (generated offline, one-time, stored in HSM)
openssl genrsa -out policy-sign.pem 2048
openssl rsa -in policy-sign.pem -pubout -out policy-sign-pub.pem

#    Load public key into TPM
tpm2_loadexternal -C o -G rsa -u policy-sign-pub.pem -c policy-key.ctx -n policy-key.name

#    Create authorized policy
tpm2_startauthsession -S session.ctx
tpm2_policyauthorize -S session.ctx -n policy-key.name -L authorized.policy
tpm2_flushcontext session.ctx

# 3. Generate LUKS key and seal
dd if=/dev/urandom bs=32 count=1 of=luks-key.bin
tpm2_create -C primary.ctx -i luks-key.bin \
  -L authorized.policy \
  -u sealed.pub -r sealed.priv
tpm2_load -C primary.ctx -u sealed.pub -r sealed.priv -c sealed.ctx
tpm2_evictcontrol -C o -c sealed.ctx 0x81000001   # persistent handle

# 4. Sign initial PCR policy (done by CI/CD)
tpm2_startauthsession -S session.ctx
tpm2_policypcr -S session.ctx -l sha256:0,4 -L pcr.policy
tpm2_flushcontext session.ctx
openssl dgst -sha256 -sign policy-sign.pem -out pcr.policy.sig pcr.policy

# === UNSEAL AT EVERY BOOT (initramfs) ===

# 1. Load public key
tpm2_loadexternal -C o -G rsa -u policy-sign-pub.pem -c policy-key.ctx

# 2. Verify signed PCR policy
tpm2_verifysignature -c policy-key.ctx -g sha256 \
  -m pcr.policy -s pcr.policy.sig -t verification.ticket

# 3. Build policy session
tpm2_startauthsession --policy-session -S session.ctx
tpm2_policypcr -S session.ctx -l sha256:0,4
tpm2_policyauthorize -S session.ctx \
  -n policy-key.name -t verification.ticket

# 4. MANDATORY: Parameter Encryption (F1 — SoftSPI bus protection)
tpm2_startauthsession --hmac-session --tpmkey-context primary.ctx --session enc.ctx
tpm2_sessionconfig --enable-encrypt enc.ctx

# 5. Unseal with encrypted response → pipe to cryptsetup
tpm2_unseal -c 0x81000001 -p session:session.ctx --session enc.ctx | \
  cryptsetup luksOpen /dev/mmcblk0p3 data --key-file=-
tpm2_flushcontext session.ctx
tpm2_flushcontext enc.ctx
```

**Without Parameter Encryption:** Key travels in cleartext over SoftSPI bus →
Logic Analyzer capture possible. **All unseal operations MUST use encrypted
sessions** (F1).

**Validation tests for PolicyAuthorize:**
1. Create policy signing key → seal with authorized policy → sign PCR policy → unseal → success
2. Change PCR values, sign new policy → unseal → success
3. Change PCR values, do NOT sign new policy → unseal → failure expected

### 3.5 FDE Prototype (Full Disk Encryption)

**Implementation: snapd FDE hooks** (not custom solution).

snapd checks encryption in this order:
1. Kernel snap has `fde-setup` hook? → **Use hook** ← our solution
2. OP-TEE TA present? → Use OP-TEE
3. Neither? → Built-in secboot (needs UEFI+TPM)

The docs limitation "External I2C/SPI-based TPM modules are not currently
supported" refers to snapd's built-in secboot (fallback path 3), NOT the
FDE hooks. Hook detection is trivial: `kernelInfo.Hooks["fde-setup"]`.

**What to implement in kernel snap:**
- `meta/hooks/fde-setup` — Called at installation. Receives key via
  `snapctl fde-setup-request`, seals with SoftSPI-TPM, returns sealed-key
  + handle via `snapctl fde-setup-result`.
- `fde-reveal-key` — In initramfs, called at every boot. Receives sealed-key
  + handle via stdin (JSON), unseals with TPM (using Parameter Encryption!),
  returns plaintext key on stdout (JSON).

Advantage: Full snapd integration (recovery keys, reinstall keys,
snap-bootstrap flow, `snap recovery --show-keys` etc.)

Reference implementation (test): `ext_refs/snapd/tests/lib/fde-setup-hook/fde-setup.go`
Secboot hook interface: `ext_refs/snapd/secboot/secboot_hooks.go`
FDE hook API: `ext_refs/snapd/kernel/fde/fde.go`

**Persistent handles vs filesystem blobs (hybrid approach):**
- FDE key: **persistent handle** (0x81000001) — must be available before filesystem mount
- License keys: **filesystem blobs** (seal.pub + seal.priv) — loaded after boot
- SLB9672 persistent handle limit: typically 3-7 slots — use sparingly

**Recovery:**
- Production: Factory reset instead of recovery passphrase. Device gets reflashed
  and re-provisioned. Data is lost (F10).
- Development/validation: Set LUKS passphrase as backup slot for debugging.

### 3.6 License Key Prototype

For software license keys, API keys, device credentials that should only be
available on the provisioned device in the expected boot state:

```bash
# === PROVISIONING (Factory or Admin-Frontend) ===

# License key sealed
echo -n "NANOTEC-LIC-2026-XXXX-YYYY" > license.txt
tpm2_createprimary -C o -c primary.ctx
tpm2_create -C primary.ctx \
  -i license.txt \
  -L authorized.policy \
  -u license.pub -r license.priv \
  -p <auth-value>
tpm2_load -C primary.ctx -u license.pub -r license.priv -c license.ctx
tpm2_evictcontrol -C o -c license.ctx 0x81000002

# === RUNTIME (Application reads license) ===

# Unseal only possible if boot state matches
tpm2_startauthsession --policy-session -S session.ctx
tpm2_policypcr -S session.ctx -l sha256:0,4
tpm2_policyauthorize -S session.ctx -n policy-key.name -t verification.ticket

# Parameter Encryption for bus protection
tpm2_startauthsession --hmac-session --tpmkey-context primary.ctx --session enc.ctx
tpm2_sessionconfig --enable-encrypt enc.ctx

LICENSE=$(tpm2_unseal -c 0x81000002 -p session:session.ctx --session enc.ctx)
tpm2_flushcontext session.ctx
tpm2_flushcontext enc.ctx

# $LICENSE is plaintext — only in RAM, never on disk
```

**Security benefit:**
- License key is never plaintext on filesystem
- Can only be decrypted on the provisioned device (TPM is hardware-bound)
- Can only be decrypted in expected boot state (PCR policy)
- SD card clone → different TPM → unseal fails
- Firmware manipulation → different PCR values → unseal fails

**Process isolation (F6):** authValue + `TPM2_PolicyCommandCode(TPM2_CC_Unseal)`
on the sealed object. Only the license application knows the authValue.

---

## Phase 4: FIT Public Key in TPM — DEFERRED

> **Decision:** Not integrated into boot flow. DTB-embedded key is already
> authenticated through hardware root of trust (OTP → boot.sig → boot.img →
> DTB → FIT public key). Commands preserved as educational reference.

### 4.1 Store Public Key in NV Index (Reference Only)

```bash
# Read the FIT signing public key (DER format)
openssl x509 -in keys/dev-sign.crt -pubkey -noout | \
  openssl pkey -pubin -outform der -out fit-pubkey.der

# Check size
wc -c fit-pubkey.der

# Define NV index for public key
tpm2_nvdefine 0x01500010 \
  --hierarchy=o \
  --size=$(wc -c < fit-pubkey.der) \
  --attributes="ownerread|ownerwrite|authread|no_da"

# Write public key
tpm2_nvwrite 0x01500010 -i fit-pubkey.der

# Read back and verify
tpm2_nvread 0x01500010 -o fit-pubkey-readback.der
diff fit-pubkey.der fit-pubkey-readback.der
```

### 4.2 Read Public Key from U-Boot (Reference Only)

```
tpm2 nv_read 0x01500010 0x02000000 <size>
md.b 0x02000000 <size>
```

---

## Phase 5: Deployment and Maintenance Lifecycle

### 5.1 Provisioning (Factory / First Boot via `install-device` Hook)

Provisioning runs in the gadget snap's `install-device` hook during both
`install` and `factory-reset` modes (confirmed in snapd `handlers_install.go`).

```bash
# 1. Clear any previous state
tpm2_clear -c p

# 2. Set owner auth (device-unique, derived from serial + EK)
#    ownerAuth = HMAC-SHA256(master-secret, device-serial || EK_pubkey_hash)
#    Master secret in manufacturer HSM, never on device
tpm2_changeauth -c o <derived-owner-auth>

# 3. Define NV counter indices
tpm2_nvdefine 0x01500001 --hierarchy=o --size=8 \
  --attributes="nt=1|ownerread|ownerwrite|authread|authwrite" \
  -p <derived-owner-auth>

tpm2_nvdefine 0x01500002 --hierarchy=o --size=8 \
  --attributes="nt=1|ownerread|ownerwrite|authread|authwrite" \
  -p <derived-owner-auth>

# 4. Set counters to seed snap versions
SEED_KERNEL_VERSION=1  # from snap metadata in ubuntu-seed
for i in $(seq 1 $SEED_KERNEL_VERSION); do
    tpm2_nvincrement 0x01500001 -P <derived-owner-auth>
done

SEED_GADGET_VERSION=1  # from snap metadata in ubuntu-seed
for i in $(seq 1 $SEED_GADGET_VERSION); do
    tpm2_nvincrement 0x01500002 -P <derived-owner-auth>
done

# 5. Configure DA lockout (F14)
tpm2_dictionarylockout --setup-parameters \
  --max-tries=5 --recovery-time=3600 --lockout-recovery-time=86400

# 6. Verify EK certificate (F15)
tpm2_nvread 0x01C00002 -o ek_cert.der  # Standard TCG NV Index
openssl verify -CAfile infineon-root-ca.pem ek_cert.der

# 7. (Optional) Disable platform hierarchy
tpm2_hierarchycontrol -C p --hierarchy-auth="" phEnable clear
```

**For initial validation (Phase 1-3):** Use a static test password.
Proper auth derivation is a production concern.

### 5.2 Runtime Operations (Every Boot)

```
U-Boot (snapd_recovery_mode="run"):
  1. tpm2 init + tpm2 startup TPM2_SU_CLEAR
  2. If TPM init fails:
     → tpm_required=1 → refuse boot (reset)
     → tpm_required=0 → continue without TPM (development)
  3. Read NV counter 0x01500001 → compare with FIT image version
  4. Read NV counter 0x01500002 → compare with boot.img version
  5. Version < counter → rollback → refuse boot
  6. Version >= counter → proceed
  7. FIT signature verification (existing flow)
  8. Extend PCR with boot measurements

U-Boot (snapd_recovery_mode="install" / "factory-reset"):
  → No TPM checking — UC handles provisioning via install-device hook

Linux:
  9. /dev/tpm0 available
  10. FDE unseal via fde-reveal-key (if FDE enabled)
  11. After successful OTA: post-refresh hook increments relevant NV counter
  12. License key unseal at application runtime (if licensed)
```

### 5.3 OTA Update Flow

Counter increment via `post-refresh` hooks (Phase 1.4):
- Kernel snap update → post-refresh increments counter 0x01500001
- Gadget snap update → reboot → post-refresh increments counter 0x01500002

**Failure handling:**
- Hook runs only after confirmed successful boot → counter never incremented
  for failed updates
- Counter is monotonic — cannot roll back the counter itself
- If increment fails (TPM error): counter stays at old value, next boot still
  passes (version >= old counter). Hook retries on next boot cycle.

### 5.4 Factory Reset / Reinstall — TPM Reset

**The problem:** Factory reset and reinstall use snaps from **ubuntu-seed**
(original versions from initial installation), not the currently refreshed
versions. TPM counter reflects the latest OTA version.

```
Example:
  Initial install: Kernel v1 → Counter = 1
  OTA updates: Kernel v5 → Counter = 5
  Factory Reset: → Kernel v1 (from ubuntu-seed) → Counter 5 > v1 → BOOT BLOCKED!
```

**Solution: TPM reset during factory-reset/install via `install-device` hook.**

| Mode | ubuntu-seed | ubuntu-save | ubuntu-data | TPM State | Result |
|------|-------------|-------------|-------------|-----------|--------|
| **run** | unchanged | unchanged | unchanged | unchanged | Normal operation |
| **factory-reset** | unchanged | preserved (resealed) | **deleted** | **must be reset** | Seed snaps boot |
| **install** (reinstall) | unchanged | **deleted** | **deleted** | **must be reset** | Seed snaps boot, new serial |
| **manual reflashing** | new | new | new | **must be reset** | New image boots |

**Factory Reset flow (`snapd_recovery_mode=factory-reset`):**
1. System boots in factory-reset mode (initramfs)
2. snap-bootstrap performs partition reset
3. `install-device` hook (gadget snap) runs:
   a. `TPM2_Clear` → all NV objects deleted
   b. ownerAuth re-set (device-unique derivation)
   c. NV counters redefined (0x01500001 + 0x01500002)
   d. Counters incremented to seed snap versions
   e. DA lockout configured
   f. FDE keys re-sealed (if FDE active)
4. Reboot in run mode
5. U-Boot checks counter vs seed version → match!
6. After first successful boot: OTA can refresh snaps

**Reinstall flow (`snapd_recovery_mode=install`):**
Identical to factory reset, plus: ubuntu-save deleted → new device keys,
new device serial → device must be re-registered, licenses must be reinstalled.

**Manual reflashing flow:**
1. New image flashed to eMMC → `snapd_recovery_mode=install`
2. U-Boot takes install path → no TPM checking
3. `install-device` hook provisions TPM from scratch
4. Counters set to new image version
5. Normal operation

**TPM reset detection in run mode (F5):**
UC manages boot paths via `snapd_recovery_mode` — no custom provisioned
marker needed. U-Boot only checks TPM counters in `run` mode. If counter
read fails in `run` mode → TPM was reset → `tpm_required=1` → boot refused.

### 5.5 Recovery / Failure Scenarios

| Scenario | Behavior |
|----------|----------|
| TPM unreachable (HW failure) | `tpm_required=1`: refuse boot. `tpm_required=0`: boot without counter check |
| NV counter ahead of image version | Rollback detected → refuse boot |
| TPM reset/cleared in run mode | Counter read fails → refuse boot (tpm_required=1) |
| Factory reset / reinstall | install-device hook re-provisions TPM (5.4) |
| Manual reflashing | install path → TPM re-provisioned |

### 5.6 Key and Counter Limits

- SLB9672 NV write endurance: typically 100,000–200,000 writes per index
  → At one kernel update per day: ~274–548 years. Not a concern.
- Maximum NV indices: TPM spec minimum 8, SLB9672 supports more. Document actual limit.
- Counter overflow: 64-bit counter → effectively unlimited
- Persistent handle limit: typically 3-7 slots on SLB9672 — use sparingly

### 5.7 License Architecture — Separate Delivery

Licenses are delivered **separately from device image**. Customer installs
licenses via admin frontend.

**Why separate:**
1. Factory reset = no support case — customer can self-re-license
2. Device replacement: license transferable (after de-registration)
3. Flexibility: different license tiers without image change
4. No license loss in image: image contains no licenses → freely distributable

**Flow:**
```
After installation / factory reset:
  1. Device boots with base image → base functionality (no licensed features)
  2. Customer logs into admin frontend (Web-UI or API)
  3. Enters license token (received at purchase)
  4. Admin frontend → API call to device → license validation against license server
  5. License server confirms: "Token valid, activation allowed"
  6. Device seals license data in TPM (PCR-policy bound)
  7. License active → extended features unlocked

Every boot:
  8. Application unseals license from TPM (with Parameter Encryption)
  9. Checks: license valid? → unlock features
  10. Optional: periodic heartbeat to license server (online validation)

Factory reset:
  11. TPM cleared → sealed license objects deleted
  12. Device boots without license → base functionality
  13. Customer re-licenses via admin frontend (step 2-7)
  14. License server: "Activation #2 for this token" (check limit)
```

| Situation | TPM State | License | Action needed |
|-----------|-----------|---------|---------------|
| Normal operation | Sealed object present | Active | None |
| Factory reset | TPM cleared, object gone | Inactive | Customer re-licenses via admin frontend |
| Reinstall | TPM cleared, new serial | Inactive | Customer re-licenses + possibly new token |
| Manual reflashing | TPM cleared | Inactive | Customer re-licenses |
| TPM HW defect | TPM unreachable | Fallback | Base functionality or offline grace period |

---

## Additional: U-Boot Bootcount (Boot Failure Detection)

Separate from anti-rollback but complementary. U-Boot has built-in
`CONFIG_BOOTCOUNT_LIMIT` mechanism:
- `bootcount` increments each boot, reset to 0 by Linux after successful boot
- If `bootcount > bootlimit` → execute `altbootcmd` instead of `bootcmd`
- `upgrade_available` flag gates the mechanism (only active during upgrades)
- Storage: `CONFIG_BOOTCOUNT_ENV` (env var) or `CONFIG_BOOTCOUNT_FS` (FAT file)

This is for detecting "boot keeps failing, fall back" — not anti-rollback.
Consider adding as second layer alongside TPM counters. Not blocking for
initial TPM validation.

---

## Security Audit — Findings and Decisions

Systematic review against TCG TPM 2.0 Guidelines, NIST SP 800-147/155,
OWASP IoT Top 10, ARM PSA, IEC 62443.

**Context assumptions for risk assessment:**
- Target product: eMMC-only (no SD card slot)
- Boot only reachable via signed bootloader (OTP + boot.sig)
- No USB boot without OTP programming
- CI/CD already has HSM for image signing

| # | Finding | Severity | Decision |
|---|---------|----------|----------|
| F1 | SoftSPI bus eavesdropping | Critical→Mitigated | Parameter Encryption mandatory for all unseal ops. U-Boot reads no secrets. SoftSPI stays (pin constraints). |
| F2 | ownerAuth in U-Boot env | High→Eliminated | Removed. `authread` allows unauthenticated counter read. ownerAuth only in Linux. |
| F3 | PCR selection sha256:0,4 | High→Accepted | Sufficient. bootargs compiled in signed boot.img. cmdline.txt in boot.img. |
| F4 | Policy signing key compromise | High→Mitigated | Key in existing build HSM. CI/CD pre-signs PCR policies. Predictable PCR values. |
| F5 | Owner hierarchy clearable (TPM2_Clear) | High→Mitigated | UC `snapd_recovery_mode` is the marker. No custom provisioned flag. Counter read failure in run mode = boot refused. |
| F6 | License key process isolation | Medium | authValue + PolicyCommandCode(Unseal) on sealed object. |
| F7 | Measurement sequence undefined | Medium | Document PCR 0,4,9,11 assignments before Phase 3.4. Verify event log. |
| F8 | Counter increment race (>= vs >) | Medium | `>=` for validation, `>` for production. |
| F9 | HMAC derivation with public input | Medium | EK pubkey hash as additional input in ownerAuth derivation. |
| F10 | LUKS recovery passphrase | Medium | Factory reset instead of recovery passphrase (production). Passphrase for development only. |
| F11 | No remote attestation | Medium | Post-launch feature (V2). TPM2_Quote + fleet management. |
| F12 | SoftSPI kernel driver attack surface | Medium | Mitigated by Parameter Encryption (F1). HW-SPI as future improvement. |
| F13 | U-Boot stop string "changeme" | Low | Change before production. Check `CONFIG_AUTOBOOT_ENCRYPTION=y`. |
| F14 | DA lockout not configured | Low | Added to provisioning script. max-tries=5, recovery-time=3600s. |
| F15 | TPM authenticity not verified | Low | EK certificate verification at provisioning. EK hash input to ownerAuth derivation. |

---

## Recommended Phase Execution Order

| Priority | Phase | Status | Notes |
|----------|-------|--------|-------|
| 1 | Phase 1.1-1.2 | Ready | NV counter ops in Linux — no patches needed |
| 2 | Phase 2.1-2.3 | Ready | PCR read/extend — existing U-Boot commands work |
| 3 | Phase 3.1-3.3 | Ready | Basic seal/unseal + PCR policy — Linux tpm2-tools |
| 4 | Phase 2.4 | Ready | PCR policy creation — Linux tpm2-tools only |
| 5 | Phase 3.4 | Ready | PolicyAuthorize — production seal flow |
| 6 | Phase 3.5-3.6 | Ready | FDE prototype + license key prototype |
| 7 | Phase 1.3 | Blocked | U-Boot NV read — needs `cmd/tpm-v2.c` CLI wrapper patch |
| 8 | Phase 1.4 | Ready | NV counter increment via snap `post-refresh` hook |
| 9 | Phase 5.1 | Ready | Provisioning script — Linux tpm2-tools |
| 10 | Phase 1.4 + 5.2-5.4 | After patches | Full boot integration — needs U-Boot patches + rpi-uc.env |
| ~~X~~ | ~~Phase 4~~ | ~~Deferred~~ | ~~FIT key in TPM — DTB key stays, protected by boot.sig~~ |

**Key files to modify (implementation phase):**
- `u-boot/cmd/tpm-v2.c` — Add `nv_read`, `nv_write`, `nv_increment` CLI wrappers
- `u-boot/lib/tpm-v2.c` — Implement `tpm2_nv_increment()`, fix hierarchy (platform→owner)
- `u-boot/include/tpm-v2.h` — Add `TPM2_CC_NV_INCREMENT = 0x0134` + prototypes
- `boot-img/u-boot/board/raspberrypi/rpi/rpi-uc.env` — TPM startup + counter check (read-only)
- `boot-img/u-boot/configs/rpi_sb_uc_defconfig` — TPM Kconfig options
- Kernel snap `meta/hooks/post-refresh` — NV counter increment after confirmed boot
- Kernel snap `meta/hooks/fde-setup` — FDE key sealing via TPM
- Kernel snap `fde-reveal-key` binary — FDE key unsealing in initramfs
- Gadget snap `meta/hooks/install-device` — TPM provisioning (install + factory-reset)
- Gadget snap `meta/hooks/post-refresh` — Gadget NV counter increment

---

## Deliverables

After completing all phases, produce:
1. Tested commands and expected outputs for each operation
2. This document updated with hardware test results
3. Provisioning script (or runbook) for factory setup
4. Updated `rpi-uc.env` commands for TPM boot flow (Phase 5.2)
5. U-Boot patches for NV CLI commands (Phase 1.3)
6. `fde-setup` hook + `fde-reveal-key` binary (Phase 3.5)
7. `post-refresh` hooks for kernel and gadget snaps (Phase 1.4)
8. `install-device` hook with TPM provisioning (Phase 5.1)

## Verification Checklist

1. Review NV attribute strings against `man tpm2_nvdefine`
2. Validate U-Boot patch approach by reading `cmd/tpm-v1.c:312-375` as reference
3. Test Phase 1.1-1.2 on hardware with owner hierarchy + static test password
4. Verify Parameter Encryption with SLB9672: `tpm2_startauthsession --hmac-session` + `tpm2_sessionconfig --enable-encrypt`
5. Verify `authread` NV attribute allows unauthenticated read (U-Boot needs no ownerAuth)
6. Test TPM reset detection: clear TPM in `run` mode → counter read fails → `tpm_required=1` → boot refused
7. Test factory reset flow: TPM clear → counter redefined → incremented to seed version → boot OK
8. Test reinstall flow: everything deleted → TPM clear → counter new → boot OK → license missing → re-license
9. Test license flow: install license → seal in TPM → reboot → unseal → license active → factory reset → license gone → re-install
