# Tasks — iot-u-boot

Nanotec-owned work on the vendored U-Boot fork (Raspberry Pi custodians upstream). Only
Nanotec / Sydney-Krauss commits since the last vendor merge (rpi-2026.04-rc4) are tracked here —
the upstream U-Boot history is NOT counted.

## Open

### 2026-07-02 · secure-boot · HIGH — Complete the U-Boot side of the end-to-end secure-boot chain on real CM5 hardware: FIT signature verification against the burned OTP key (not just dev-key), rpi-uc.env + dev-sign.crt coupling. Part of the grade=dangerous → signed transition across gadget/kernel/master/u-boot.

### 2026-07-02 · maintenance · MEDIUM — Rebase the Nanotec patch set onto the next upstream U-Boot tag when it lands; re-run the env-import validation regression suite after the rebase.

## Planned

## Closed

### 2026-06-01 · env — Factor common kernel params into a kernel_cmdline variable; add full kernel cmdline to rpi-uc bootargs
commit: 4a0ffae · note: latest

### 2026-05-01 · testing — env-import validation regression suite + GitHub Actions CI; validation docs
commit: 211a691

### 2026-05-01 · hardening — env import -v: whitespace + shell-metacharacter + newline/VT/FF validation via himport_r() validate parameter
commit: 4f22d06

### 2026-04-01 · secure-boot — rpi_sb_uc_defconfig + compiled-in UC boot environment for Ubuntu Core secure boot
commit: 69d9ec2

### 2026-04-01 · docs — UC secure-boot documentation under doc/nanotec/ (README-uc-secureboot, README-testing)
commit: 0c765e7
