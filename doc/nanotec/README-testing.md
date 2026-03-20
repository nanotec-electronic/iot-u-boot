# Testing — Pi5 UC Secure Boot

## Framework Strategy (future automation)

**testflinger** handles device provisioning: flashes `pi.img`, power cycles, captures serial log, hands SSH to test runner.

**spread** handles test orchestration: defines suites as shell tasks with prepare/execute/restore lifecycle, runs against the testflinger backend.

```
CI build → artifact store (pi.img, snaps)
  └── testflinger job
        ├── provision: flash pi.img, capture serial → /tmp/serial.log
        └── test: spread testflinger:nanotec-pi5:tests/...
              ├── boot-chain/    (assert on serial.log)
              ├── a-b-kernel/    (SSH + ubuntu-boot partition writes)
              ├── security/      (serial-log + SSH)
              └── uc-lifecycle/  (SSH, snapd interactions)
```

Serial-based tests (U-Boot pre-SSH) assert against the serial.log captured during the provision phase. Interactive tests (e.g. autoboot stop key) use a ser2net TCP proxy from the testflinger agent.

### Planned test suite map

| ID | Suite | Description |
|----|-------|-------------|
| B01 | boot-chain | Clean install → FIT sig verified → UC boots |
| B02 | boot-chain | Tampered FIT → FATAL + reset |
| B03 | boot-chain | boot.scr on partition → ignored |
| B04 | boot-chain | Missing kernel.img → RESET_TO_RETRY within 60s |
| B05 | boot-chain | No autoboot prompt (keyed stop only) |
| K01 | a-b-kernel | Normal run mode (empty kernel_status) |
| K02 | a-b-kernel | try → trying transition |
| K03 | a-b-kernel | trying → rollback |
| K05 | a-b-kernel | try with empty snap_try_kernel → fallback |
| SEC01 | security | boot.sel whitespace injection rejected by -v |
| SEC02 | security | boot.sel bad CRC → import failed |
| SEC03 | security | Extra var in boot.sel not imported (whitelist) |
| SEC04 | security | boot.img tampered → firmware refuses (pre-U-Boot) |
| UC01 | uc-lifecycle | snap refresh kernel → try/trying/clear cycle |
| UC02 | uc-lifecycle | boot.sel kernel_status cleared after successful refresh |
| UC04 | uc-lifecycle | SSH with system-user key works |

---

## Sandbox Tests (Automated)

U-Boot's test framework runs against a sandbox build (native x86_64 binary,
no hardware required). This covers env import/export, FIT handling, hush shell,
filesystem ops, and our custom UC bootsel integration tests.

### Build sandbox

```bash
make sandbox_defconfig
scripts/config --disable CONFIG_EFI_CAPSULE_AUTHENTICATE
make olddefconfig
make -j$(nproc)
```

Note: EFI capsule auth is disabled (requires `efitools` and is irrelevant — we
disabled EFI entirely in our defconfig).

### Run tests

```bash
# Focused sandbox suite (~1250 tests, ~2.5 min):
# Excludes EFI/capsule, Android, VBE/VPL/SPL, Zynq/FPGA/TPM,
# network, DFU/UMS, and other irrelevant platform tests.
./test/py/test.py --bd sandbox --build-dir . \
  -k "not efi and not capsule and not test_dm and not test_pinmux \
      and not abootimg and not test_avb and not test_vbe \
      and not test_vpl and not test_spl and not test_handoff \
      and not test_ofplatdata and not zynq and not test_qfw \
      and not test_tpm2 and not test_scp03 and not test_optee \
      and not test_fpga and not test_dfu and not test_ums \
      and not test_upl and not test_fw_handoff and not test_mdio \
      and not test_mii and not test_scsi and not test_smbios \
      and not test_trace and not test_pstore and not test_distro \
      and not test_extension and not test_lsblk and not test_sleep \
      and not test_net_boot and not test_net and not test_ab"

# UC bootsel integration tests only (8 tests):
./test/py/test.py --bd sandbox --build-dir . -k test_bootsel

# env import -v validation + whitelist tests (22 tests):
./test/py/test.py --bd sandbox --build-dir . \
  -k "test_env_import_validate or test_env_import_no_validate or test_env_import_whitelist"
```

### UC bootsel tests (`test/py/tests/test_uc_bootsel.py`)

| Test | What it verifies |
|------|-----------------|
| `test_bootsel_ab_try_to_trying` | A/B: try to trying transition |
| `test_bootsel_ab_trying_to_cleared` | A/B: trying to cleared (rollback) |
| `test_bootsel_ab_normal_boot` | A/B: normal boot, no state change |
| `test_bootsel_export_roundtrip` | export -c then import -v -c roundtrip |
| `test_bootsel_sequential_import_isolation` | Two-stage import (seed then boot) doesn't cross-contaminate |
| `test_bootsel_run_mode_bootargs` | Run mode bootargs correct |
| `test_bootsel_install_mode_bootargs` | Install mode bootargs correct |
| `test_bootsel_kernel_path_construction` | Kernel path prefix derived correctly |

### Validation & whitelist tests (`test/py/tests/test_env.py`)

| Test | What it verifies |
|------|-----------------|
| `test_env_import_validate_accepts_clean` | `-v` accepts clean values (4 parametrized: words, numbers, paths, snap names) |
| `test_env_import_validate_rejects_unsafe` | `-v` rejects unsafe chars (11 parametrized: space, tab, CR, LF, VT, FF, `;`, `\|`, `&`, `$`, `` ` ``, `(`) |
| `test_env_import_no_validate_accepts_metachar` | Control: without `-v`, metacharacters pass through |
| `test_env_import_whitelist_with_validation` | `-v` + whitelist combined: clean imported, unsafe rejected, unlisted blocked |
| `test_env_import_whitelist_blocks_unlisted` | Variables not in whitelist are blocked from import |

### CI

GitHub Actions runs automatically on push/PR (`.github/workflows/test.yml`):
- **sandbox-tests**: full sandbox suite minus EFI/DM/pinmux
- **cross-compile-check**: builds `rpi_sb_uc_defconfig` with aarch64 toolchain

---

## Manual Test Runbook

### Prerequisites

- Pi flashed with `output/pi.img`, booted to UC run mode
- SSH accessible via system-user key
- Serial connected (115200 baud, ttyAMA10)
- Current kernel snap: `nanotec-pi5-kernel_1.0_rpi-arm64.snap`

### Inspect boot.sel

```bash
# ubuntu-boot (run mode state: snap_kernel, kernel_status, snap_try_kernel)
sudo mount /dev/disk/by-label/ubuntu-boot /mnt
sudo dd if=/mnt/uboot/ubuntu/boot.sel bs=4096 count=1 2>/dev/null | strings
sudo umount /mnt

# ubuntu-seed (recovery mode state: snapd_recovery_mode, snapd_recovery_system)
sudo mount /dev/disk/by-label/ubuntu-seed /mnt
sudo dd if=/mnt/uboot/ubuntu/boot.sel bs=4096 count=1 2>/dev/null | strings
sudo umount /mnt
```

---

## T1: Good kernel update — A/B happy path

**Goal**: New kernel snap installs, U-Boot transitions try→trying, boots new kernel, snapd confirms adoption.

**Step 1 — Baseline**
```bash
ssh user@<pi>
snap list nanotec-pi5-kernel   # note current revision
snap changes
# inspect ubuntu-boot boot.sel: snap_kernel=..., kernel_status= (empty)
```

**Step 2 — Build v2 snap (same kernel, bumped version)**
```bash
# build host:
echo "1.1" > nanotec-pi5-kernel/snap-version
cd nanotec-pi5-kernel && snapcraft --verbose --platform rpi-arm64
```

**Step 3 — Install**
```bash
scp nanotec-pi5-kernel_1.1_rpi-arm64.snap user@<pi>:
ssh user@<pi>
sudo snap install --dangerous nanotec-pi5-kernel_1.1_rpi-arm64.snap
# snapd writes kernel_status=try, snap_try_kernel=<rev2>, reboots
```

**Step 4 — Serial expected output**
```
env import -v -c ...
kernel_status trying               <- U-Boot transitions try→trying, writes back
prefix=uboot/ubuntu/<rev2>/        <- boots try kernel
Verifying FIT signature...
sha256,rsa2048:dev-sign+ OK
Booting verified kernel...
```

**Step 5 — Verify adoption**
```bash
snap changes                       # should show "Done"
snap list nanotec-pi5-kernel       # rev 2 active
# boot.sel: snap_kernel=<rev2>, kernel_status= (empty), snap_try_kernel= (cleared)
```

**PASS**: System runs new kernel, boot.sel shows cleared status.

---

## T2: Bad kernel update — rollback path

**Goal**: Corrupted FIT rejected by U-Boot → reset → second boot clears `trying` → rollback to old kernel.

**Step 1 — Produce bad kernel snap**
```bash
# build host:
unsquashfs nanotec-pi5-kernel_1.0_rpi-arm64.snap
# Corrupt 1 byte at offset 1024 in the FIT (inside signed region)
dd if=/dev/urandom of=squashfs-root/kernel.img bs=1 count=1 seek=1024 conv=notrunc
mksquashfs squashfs-root nanotec-pi5-kernel_bad.snap -comp xz -noappend
```

**Step 2 — Install**
```bash
scp nanotec-pi5-kernel_bad.snap user@<pi>:
ssh user@<pi>
sudo snap install --dangerous nanotec-pi5-kernel_bad.snap
# snapd extracts corrupted kernel.img to ubuntu-boot, sets kernel_status=try, reboots
```

**Step 3 — Serial: boot cycle 1 (bad kernel attempt)**
```
kernel_status trying               <- transitions try→trying, writes back
prefix=uboot/ubuntu/<bad-rev>/
Verifying FIT signature...
FATAL: FIT signature verification failed
(reset)
```

**Step 4 — Serial: boot cycle 2 (rollback)**
```
kernel_status=trying → cleared     <- U-Boot clears, falls back to snap_kernel
prefix=uboot/ubuntu/<rev1>/
sha256,rsa2048:dev-sign+ OK
Booting verified kernel...
```

**Step 5 — Verify**
```bash
snap changes                       # shows refresh "Error" with rollback note
snap list nanotec-pi5-kernel       # back to rev 1
# boot.sel: snap_kernel=<rev1>, kernel_status= (empty)
```

**PASS**: One reset on bad kernel, old kernel boots on retry, snapd reports failure.

---

## T3: Recovery mode

**Goal**: `snap reboot --recover` routes U-Boot to `systems/<label>/kernel/` path (ubuntu-seed), not ubuntu-boot.

**Step 1 — Trigger recovery**
```bash
ssh user@<pi>
snap recovery                      # note system label (e.g. "20241201")
sudo snap reboot --recover
# snapd writes snapd_recovery_mode=recover + snapd_recovery_system=<label> to ubuntu-seed boot.sel
```

**Step 2 — Serial expected output**
```
snapd_recovery_mode=recover        <- imported from ubuntu-seed boot.sel
prefix=systems/<label>/kernel/     <- routes to ubuntu-seed (NOT uboot/ubuntu/)
Verifying FIT signature...
sha256,rsa2048:dev-sign+ OK
Booting verified kernel...
```

**Step 3 — Verify recovery environment**
```bash
cat /proc/cmdline | grep snapd_recovery_mode
# expected: snapd_recovery_mode=recover snapd_recovery_system=<label>
```

**Step 4 — Return to run mode**
```bash
sudo snap reboot --run
# Serial: snapd_recovery_mode=run, routes back to ubuntu-boot, boots installed kernel
```

**PASS**: Recovery boots from `systems/<label>/` path, FIT verified, returns to run mode cleanly.
