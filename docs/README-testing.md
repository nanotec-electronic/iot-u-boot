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

## Manual Test Runbook

### Prerequisites

- Pi flashed with `output/pi.img`, booted to UC run mode
- SSH accessible via system-user key
- Serial connected (115200 baud, ttyAMA10)
- Current kernel snap: `nanotec-pi5-kernel_1.0_rpi-amd64.snap`

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
cd nanotec-pi5-kernel && snapcraft --verbose --platform rpi-amd64
```

**Step 3 — Install**
```bash
scp nanotec-pi5-kernel_1.1_rpi-amd64.snap user@<pi>:
ssh user@<pi>
sudo snap install --dangerous nanotec-pi5-kernel_1.1_rpi-amd64.snap
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
unsquashfs nanotec-pi5-kernel_1.0_rpi-amd64.snap
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
