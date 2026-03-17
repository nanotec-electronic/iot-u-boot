# Ubuntu Core Secure Boot — Compiled Environment

> **SECURITY NOTICE**: This document describes security-critical boot chain
> modifications including U-Boot source patches, environment compilation, and
> defconfig hardening. These changes directly affect the integrity of the
> verified boot chain. **All code, configuration, and architecture decisions
> described here must be reviewed by a qualified security expert before
> deployment to production.** Incorrect implementation can silently break the
> chain of trust or introduce exploitable attack vectors.
>
> The U-Boot patches and compiled-in boot environment were ported from the
> [Canonical iot-field-gadget-snap](https://github.com/canonical/iot-field-gadget-snap)
> NXP/i.MX secure boot reference implementation and adapted to the Raspberry
> Pi 5 (BCM2712) boot flow. The original patches target NXP HAB; this
> adaptation replaces HAB-specific logic with the Pi firmware OTP + boot.sig
> verification chain and the BCM2712 split-bootm workaround.

This document describes the secure boot architecture where all boot logic
is compiled into U-Boot's default environment, eliminating the unverified
`boot.scr` attack surface.

## Background: Why Not boot.scr

A common U-Boot setup loads `boot.scr` from the boot partition at runtime.
In a secure boot chain, this is a problem:

```
Pi Firmware → verify boot.img (OTP) → U-Boot starts
  → load boot.scr from partition (UNVERIFIED!)
  → source boot.scr → load FIT → verify signature → boot kernel
```

`boot.scr` sits on the SD card partition alongside `boot.img` but is **not**
covered by `boot.sig` verification. An attacker with physical access can
replace `boot.scr` to:

- Skip FIT signature verification entirely
- Redirect boot to a malicious kernel
- Inject arbitrary kernel command line arguments
- Disable security checks

## Architecture: Compiled-In Boot Flow

All boot logic is part of the U-Boot binary itself:

```
Pi Firmware → verify boot.img (OTP) → U-Boot starts (env compiled in)
  → BOOTCOMMAND="run boot_uc" (compiled, verified)
  → boot_uc → load_uc → loadfiles (all compiled env vars)
  → load FIT → verify signature → boot kernel
```

Since U-Boot lives inside `boot.img` (verified by `boot.sig` + OTP), the
compiled environment is part of the trusted chain. No on-disk script needed.

## Files

| File | Purpose |
|------|---------|
| `board/raspberrypi/rpi/rpi-uc.env` | Compiled-in UC boot flow env vars |
| `configs/rpi_sb_uc_defconfig` | Hardened defconfig |

## Boot Flow Detail

### Entry Point

```
CONFIG_BOOTCOMMAND="run boot_uc"
```

### boot_uc

1. Calls `run load_uc` (snapd boot.sel protocol + kernel selection)
2. Verifies FIT signature: `bootm start ${fit_addr}#standard`
3. BCM2712 split-bootm workaround: `bootm loados` + `bootm ramdisk`
4. Calculates ramdisk size: `setexpr ramdisk_size ${initrd_end} - ${initrd_start}`
5. Boots kernel: `booti 0x200000 ${initrd_start}:${ramdisk_size} ${fdt_addr}`

### load_uc (Snapd Boot Protocol)

1. Load `boot.sel` from ubuntu-seed (partition 1) into `${scriptaddr}`
2. Import recovery vars: `env import -v -c ${scriptaddr} ${filesize} ${recovery_vars}`
   - Only imports: `snapd_recovery_mode`, `snapd_recovery_system`, `snapd_recovery_kernel`
   - `-v` flag rejects values with whitespace (injection prevention)
   - `-c` flag: checksum-protected binary format (see below)
3. Check `snapd_recovery_mode`:

**Run mode** (normal boot):
- Load `boot.sel` from ubuntu-boot (partition 2)
- Import kernel vars: `snap_kernel`, `snap_try_kernel`, `kernel_status`
- A/B kernel state machine:
  - `kernel_status=try` → set to `trying`, use `snap_try_kernel`
  - `kernel_status=trying` → clear status (rollback to `snap_kernel`)
  - Write back updated status via `env export -c` + `save`
- Set kernel prefix: `uboot/ubuntu/${kernel_name}/`

**Install/recovery mode**:
- Set kernel prefix: `systems/${snapd_recovery_system}/kernel/`

4. Set `bootargs` with snapd params and `panic=-1` (no `console=` — kernel uses `stdout-path` from firmware DTB)
5. Call `run loadfiles` (loads FIT image)

### loadfiles

Single operation — loads signed FIT from the determined kernel path:
```
load ${devtype} ${devnum}:${kernel_bootpart} ${fit_addr} ${prefix}${kernel_filename}
```

## boot.sel Format: CRC32-Protected Binary Environment

Snapd communicates boot parameters to U-Boot via `boot.sel` files — binary
environment blobs in U-Boot's checksum-protected format. These are created by
`mkenvimage` (from u-boot-tools) and parsed by `env import -c`.

### Binary layout

```
Offset  Size      Content
0x00    4 bytes   CRC32 checksum (covers all bytes after this field)
0x04    N bytes   KEY=VALUE\0KEY=VALUE\0...\0\0  (null-separated pairs)
0x04+N  padding   0xFF fill to fixed blob size
```

Each variable is stored as `KEY=VALUE` terminated by a single null byte (`\0`).
The block ends with a double null (`\0\0`). Unused space is filled with `0xFF`.
The 4-byte CRC32 at offset 0 covers everything from offset 4 to the end of the
blob (including padding).

### How snapd writes boot.sel

Snapd produces these files using the equivalent of:

```bash
printf "snapd_recovery_mode=install\0snapd_recovery_system=20260225\0" \
  | mkenvimage -s 4096 -o boot.sel -
```

The `-s` flag sets the total blob size. `mkenvimage` packs the key-value pairs,
pads with `0xFF`, calculates CRC32, and prepends it.

### How U-Boot reads boot.sel

`env import -c ${scriptaddr} ${filesize}` performs:

1. Read the 4-byte CRC32 from the start of the buffer
2. Calculate CRC32 over the remaining `${filesize} - 4` bytes
3. **Reject the entire import** if the checksums don't match
4. Parse `KEY=VALUE\0` pairs from the data portion
5. If a variable whitelist is given (trailing args), import only those names

### What the CRC protects against

The CRC32 is a **data integrity** check, not a cryptographic one. It catches:

- SD card bit rot or bad sectors
- Partial writes (power loss during snapd's boot.sel update)
- Naive byte-level tampering (modifying values without updating the CRC)

It does **not** stop a sophisticated attacker who modifies the data and
recalculates the CRC. That's why the `-c` check is layered with:

- **Variable whitelisting** — only named variables (`${recovery_vars}` /
  `${kernel_vars}`) are imported, everything else is ignored
- **`-v` validation** — rejects values containing whitespace, preventing
  command injection like `snapd_recovery_mode=run evil_cmd`

### boot.sel is not signed — and why that's acceptable

boot.sel **cannot** be cryptographically signed. Snapd must write to it at
runtime (A/B kernel updates, recovery mode changes), and U-Boot has no command
to verify signatures on arbitrary files — only FIT images go through RSA
verification. This is the same trade-off the NXP/iot-field reference makes and
is inherent to any system where the bootloader needs runtime-writable state
from the OS.

However, the damage from a tampered boot.sel is **extremely limited**. Even
with a perfectly crafted blob (valid CRC, no whitespace in values), an attacker
can only control these whitelisted variables:

| Variable | Attacker can set to | Actual impact |
|----------|---------------------|---------------|
| `snapd_recovery_mode` | `run`, `install`, `recover` | Forces install/recovery (DoS) |
| `snapd_recovery_system` | Any system label string | Selects recovery system (still signed) |
| `snap_kernel` | Any snap name string | Changes kernel path prefix |
| `snap_try_kernel` | Any snap name string | Changes try-kernel path prefix |
| `kernel_status` | `try`, `trying`, etc. | Manipulates A/B rollback state |

The critical constraint: **whichever kernel path the attacker selects, the
loaded FIT image must still pass RSA signature verification.** A malicious
kernel without a valid signature will be rejected by `bootm start`. If the
attacker points to a nonexistent path, the load fails and `RESET_TO_RETRY`
reboots after 60s.

The worst-case attack via boot.sel is **denial of service** (forcing an
install-mode loop or pointing to a bad path that causes repeated resets) — not
code execution. boot.sel controls _path selection_; the FIT signature controls
_code integrity_.

### NXP reference divergence: the `-r` flag

The NXP/iot-field reference gadget creates its boot.sel template with
`mkenvimage -r`, which adds a **redundant environment flag byte** at offset 4:

```
Offset  Content
0x00    4 bytes  CRC32 (covers bytes 0x05 onwards — skips the flag byte)
0x04    1 byte   Redundant env flags (active/obsolete marker)
0x05    N bytes  KEY=VALUE\0... data
```

This paired with `CONFIG_SYS_REDUNDAND_ENVIRONMENT=y` on their i.MX8MM board
(patch 0013), which makes `env import -c` expect a 5-byte header. The CRC and
the header size are consistent on both sides.

Our Pi 5 U-Boot uses `CONFIG_ENV_IS_NOWHERE=y` **without** redundant
environment. U-Boot's `env import -c` therefore casts the buffer to `env_t`
with a **4-byte header** (CRC only). If the gadget creates boot.sel with `-r`
(5-byte header), snapd sees the flag byte and creates all boot.sel files with
`HeaderFlagByte=true`. U-Boot then includes the flag byte in its CRC
calculation, which does not match the stored CRC → **"bad CRC, import
failed"**.

The fix: **omit `-r` from `mkenvimage`** in the gadget snap. Snapd reads the
gadget's boot.sel template to determine the format for all boot.sel files it
creates. A template without the flag byte means all snapd-produced files use
the 4-byte CRC header that our U-Boot expects.

### The writeback path (`env export -c`)

During A/B kernel rollback, updated state is written back:

```
env export -c ${scriptaddr} ${kernel_vars}
save ${devtype} ${devnum}:${kernel_bootpart} ${scriptaddr} ${core_state} ${filesize}
```

`env export -c` serializes only the named variables into the same CRC32-protected
format at `${scriptaddr}`, then `save` writes the buffer to disk. The `${filesize}`
from the preceding `load` ensures the output blob matches the original size.

## U-Boot Source Patches

### `env import -v` Validation (ported from NXP patch 0001)

Adds a `-v` flag to the `env import` command that validates imported values
by rejecting any containing whitespace characters. This prevents a malformed
`boot.sel` from injecting values like `snapd_recovery_mode=run evil_cmd`.

**Modified files:**

- `cmd/nvedit.c` — Parse `-v` flag, pass `validate` parameter to `himport_r()`
- `include/search.h` — Updated `himport_r()` declaration (added 9th param)
- `lib/hashtable.c` — Updated `himport_r()` definition, added validation logic
  that skips entries where the value contains space or tab characters
- `env/common.c` — 3 call sites updated (pass `0` for validate)
- `board/sunxi/board.c` — 1 call site updated (pass `0` for validate)

## Security Hardening (defconfig)

The defconfig (`rpi_sb_uc_defconfig`) includes these security measures
beyond the upstream `rpi_arm64_defconfig`:

| Config | Value | Purpose |
|--------|-------|---------|
| `CONFIG_BOOTCOMMAND` | `"run boot_uc"` | No boot.scr loading |
| `CONFIG_LEGACY_IMAGE_FORMAT` | not set | Cannot parse legacy images (boot.scr) |
| `CONFIG_ENV_IS_NOWHERE` | `y` | No persistent writable environment |
| `CONFIG_BOOT_RETRY` | `y` | Auto-retry boot on failure |
| `CONFIG_BOOT_RETRY_TIME` | `60` | Reset after 60s at prompt |
| `CONFIG_RESET_TO_RETRY` | `y` | Full board reset on retry |
| `CONFIG_AUTOBOOT_KEYED` | `y` | Password required to enter shell |
| `CONFIG_AUTOBOOT_STOP_STR` | `"changeme"` | **CHANGE THIS FOR PRODUCTION** |
| `CONFIG_USE_BOOTARGS` | `y` | Static `panic=-1` in bootargs |
| `CONFIG_EFI_LOADER` | not set | No EFI boot path |
| `CONFIG_BOOTM_EFI` | not set | No EFI bootm support |
| `CONFIG_CMD_BOOTEFI` | not set | No bootefi command |
| `CONFIG_ENV_SOURCE_FILE` | `"rpi-uc"` | Uses `rpi-uc.env` instead of `rpi.env` |

## Attack Surface Comparison

| Attack Vector | Common U-Boot (boot.scr) | This setup (compiled env) |
|---------------|---------------|---------------------|
| Replace boot.scr on SD | Boot flow hijacked | No boot.scr to replace |
| Inject via boot.sel | Unvalidated import | Whitelisted vars + `-v` validation |
| Modify saved U-Boot env | ENV_IS_IN_FAT possible | ENV_IS_NOWHERE — no saved env |
| Interrupt boot via UART | BOOTDELAY=-2 only | AUTOBOOT_KEYED + password |
| Boot hangs at prompt | Stays at shell forever | RESET_TO_RETRY after 60s |
| Kernel panic drops to shell | Default kernel behavior | `panic=-1` forces reboot |
| EFI boot bypass | EFI loader available | EFI entirely disabled |
| Parse malicious legacy image | LEGACY_IMAGE_FORMAT=y | LEGACY_IMAGE_FORMAT disabled |

## Defconfig Changes vs Upstream

Complete list of changes in `rpi_sb_uc_defconfig` compared to the upstream
U-Boot `rpi_arm64_defconfig`. Upstream is the stock defconfig for all Raspberry
Pi 64-bit boards with no secure boot or Ubuntu Core support.

### Added (not in upstream)

| Config | Value | Why |
|--------|-------|-----|
| `CONFIG_ENV_SOURCE_FILE` | `"rpi-uc"` | Use `rpi-uc.env` (compiled-in UC boot flow) instead of default `rpi.env` |
| `CONFIG_ENV_IS_NOWHERE` | `y` | Disable persistent writable environment — prevents attacker from saving malicious env to FAT |
| `CONFIG_FIT` | `y` | Enable FIT image support (kernel + initramfs in signed container) |
| `CONFIG_FIT_SIGNATURE` | `y` | Enable RSA signature verification on FIT images |
| `CONFIG_FIT_VERBOSE` | `y` | Log signature verification details (useful for debug, no security impact) |
| `CONFIG_RSA` | `y` | RSA crypto library — required by FIT_SIGNATURE |
| `CONFIG_BOOTDELAY` | `-2` | No autoboot delay — boot immediately (Pi firmware overrides this via DTB, but we set it anyway) |
| `CONFIG_AUTOBOOT_KEYED` | `y` | Require password string to interrupt boot and enter U-Boot shell |
| `CONFIG_AUTOBOOT_STOP_STR` | `"changeme"` | Autoboot password — **must change for production** |
| `CONFIG_USE_BOOTCOMMAND` | `y` | Use explicit boot command instead of distro boot scan |
| `CONFIG_BOOTCOMMAND` | `"run boot_uc"` | Entry point into the compiled-in UC boot flow |
| `CONFIG_USE_BOOTARGS` | `y` | Set static kernel command line arguments |
| `CONFIG_BOOTARGS` | `"panic=-1"` | Force immediate reboot on kernel panic (belt-and-suspenders, also set in `load_uc`) |
| `CONFIG_BOOT_RETRY` | `y` | Auto-retry boot if it fails or times out at shell |
| `CONFIG_BOOT_RETRY_TIME` | `60` | Retry timeout in seconds |
| `CONFIG_RESET_TO_RETRY` | `y` | Full board reset on retry (not just re-run boot command) |
| `CONFIG_SYS_CBSIZE` | `4096` | Command buffer size — `load_uc` is ~2250 chars when flattened (upstream default 1024 is too small) |
| `CONFIG_SYS_PBSIZE` | `4121` | Print buffer size (CBSIZE + 25 overhead) |

### Removed (present in upstream, explicitly disabled)

| Config | Upstream value | Why removed |
|--------|---------------|-------------|
| `CONFIG_EFI_RUNTIME_UPDATE_CAPSULE` | `y` | EFI capsule update not needed — attack surface reduction |
| `CONFIG_EFI_CAPSULE_FIRMWARE_RAW` | `y` | EFI capsule not needed — attack surface reduction |
| `CONFIG_EFI_LOADER` | `y` (implicit) | Entire EFI boot path disabled — we boot via FIT + booti, not EFI |
| `CONFIG_BOOTM_EFI` | `y` (implicit) | No EFI bootm support needed |
| `CONFIG_CMD_BOOTEFI` | `y` (implicit) | Remove `bootefi` command from shell |
| `CONFIG_CMD_NVEDIT_EFI` | `y` | Remove EFI variable editing command |
| `CONFIG_CMD_EFIDEBUG` | `y` | Remove EFI debug command |
| `CONFIG_USE_PREBOOT` | `y` | Disabled — headless system uses serial-only console, no vidconsole. **Note:** if HDMI/vidconsole is needed, this must be re-enabled (`pci enum; usb start;` initializes RP1 southbridge via PCIe; without it vidconsole is in a bad state and corrupts serial output) |
| `CONFIG_ENV_FAT_DEVICE_AND_PART` | `"0:1"` | No FAT-based env storage — replaced by `ENV_IS_NOWHERE` |
| `CONFIG_LEGACY_IMAGE_FORMAT` | `y` (implicit) | Cannot parse legacy mkimage scripts — closes boot.scr attack vector |

### Unchanged (same as upstream)

These configs are identical between upstream and our defconfig — they provide
the base BCM283X/Pi hardware support:

| Category | Configs |
|----------|---------|
| Architecture | `ARM`, `POSITION_INDEPENDENT`, `ARCH_BCM283X`, `TARGET_RPI_ARM64`, `NR_DRAM_BANKS=8` |
| Memory | `ENV_SIZE=0x4000`, `SYS_LOAD_ADDR=0x1000000` |
| Device tree | `DEFAULT_DEVICE_TREE="bcm2711-rpi-4-b"`, `OF_LIBFDT_OVERLAY`, `OF_BOARD_SETUP`, `FDT_SIMPLEFB` |
| PCI / Bus | `PCI`, `PCI_BRCMSTB`, `PHYS_TO_BUS` |
| GPIO | `BCM2835_GPIO`, `PINCTRL` |
| MMC / SD | `MMC_SDHCI`, `MMC_SDHCI_SDMA`, `MMC_SDHCI_BCM2835`, `MMC_SDHCI_BCMSTB` |
| USB | `USB`, `USB_XHCI_HCD`, `USB_XHCI_PCI`, `USB_DWC2`, `USB_KEYBOARD` |
| Network | `BCMGENET`, `USB_HOST_ETHER`, `USB_ETHER_LAN78XX`, `USB_ETHER_SMSC95XX`, `TFTP_TSIZE` |
| Video | `VIDEO`, `VIDEO_BCM2835`, `SYS_WHITE_ON_BLACK`, `CONSOLE_SCROLL_LINES=10` |
| DMA / DFU | `DM_DMA`, `DFU_MMC`, `SYS_DFU_DATA_BUF_SIZE`, `SYS_DFU_MAX_FILE_SIZE` |
| RNG | `DM_RNG`, `RNG_IPROC200` |
| Misc | `DM_RESET`, `MISC_INIT_R`, `SYSINFO`, `SYSINFO_SMBIOS`, `ENV_VARS_UBOOT_RUNTIME_CONFIG` |
| Shell | `SYS_PROMPT="U-Boot> "`, `CMD_GPIO`, `CMD_MMC`, `CMD_PCI`, `CMD_USB`, `CMD_FS_UUID` |
| Display | disabled: `DISPLAY_CPUINFO`, `DISPLAY_BOARDINFO`, `VIDEO_BPP8`, `VIDEO_BPP16`, `HEXDUMP`, `REQUIRE_SERIAL_CONSOLE`, `PINCTRL_GENERIC` |

## Building

```bash
cd u-boot/

# Compiled env, hardened
make mrproper
make rpi_sb_uc_defconfig
make CROSS_COMPILE=aarch64-linux-gnu- -j$(nproc)

# Verify env is compiled in
strings u-boot.bin | grep load_uc
strings u-boot.bin | grep boot.scr  # should return nothing
```

Then rebuild boot.img with the new `u-boot.bin` and re-sign via
`build-boot-img.sh`. See `README-boot-img.md` for details.

## Differences from Common U-Boot Boot Flow

A standard U-Boot setup on Raspberry Pi uses `boot.scr` — a legacy mkimage
script loaded from the boot partition at runtime. This is fine for general use
but creates a security gap in a secure boot chain:

| Aspect | Common U-Boot | This setup |
|--------|---------------|------------|
| Boot logic location | `boot.scr` on SD card partition | Compiled into U-Boot binary (`rpi-uc.env`) |
| Boot logic integrity | **Unverified** — not covered by boot.sig | Verified — inside signed boot.img |
| Boot command | `load ... boot.scr; source` | `run boot_uc` (compiled-in) |
| Legacy image parser | Enabled (needed to parse boot.scr) | Disabled (attack surface removed) |
| Environment storage | FAT partition (writable by attacker) | `ENV_IS_NOWHERE` (no persistent env) |
| Autoboot interrupt | `BOOTDELAY=-2` (still bypassable) | `AUTOBOOT_KEYED` + password required |
| Boot failure behavior | Drops to U-Boot shell indefinitely | `RESET_TO_RETRY` after 60s |
| Kernel panic behavior | Default (may drop to shell) | `panic=-1` forces immediate reboot |
| EFI boot paths | Available | Entirely disabled |

These changes are **not specific to Ubuntu Core or the gadget snap** — they
apply to any Raspberry Pi secure boot setup using U-Boot. Whether deploying a
stock image or a custom distribution, if the boot chain is verified via
OTP + boot.sig, the boot logic must live inside the signed U-Boot binary.
Leaving `boot.scr` as an unverified file on the partition undermines the
entire chain.

### Gadget snap consequences

For Ubuntu Core specifically, the gadget snap (`nanotec-pi5-gadget/`) reflects
these changes:

- `gadget.yaml` — `boot.scr` content entry removed from ubuntu-seed (nothing to ship)
- `snap/snapcraft.yaml` — `boot-scr` build part removed (nothing to compile)
- `snap/snapcraft.yaml` — `mkenvimage` uses no `-r` flag (see NXP divergence above)
- `boot-script/boot.scr.in` — kept in repo for reference, no longer used in builds

## Console Configuration

`rpi-uc.env` sets serial-only I/O for this headless deployment:

```
stdin=serial
stdout=serial
stderr=serial
```

The upstream `rpi.env` (and the NXP reference) include `vidconsole` in
`stdout`/`stderr`. On Pi 5, the vidconsole is backed by the RP1 southbridge
(a PCIe device). Without `CONFIG_USE_PREBOOT` running `pci enum`, RP1 is
uninitialized and the vidconsole driver produces corrupted output on serial —
every `printf` blocks waiting on the failed framebuffer write. Serial-only
avoids this entirely.

No `console=` in kernel bootargs: the kernel uses `stdout-path` from the
firmware-provided DTB (points to `ttyAMA10`). Both the serial console and the
framebuffer console (fbcon) are active for kernel messages. If serial-only
kernel output is needed, add `console=ttyAMA10,115200` to the `setenv
bootargs` calls in `load_uc`.

## Memory Map

```
0x00000000              — RAM base
0x00080000              — kernel_addr_r (booti relocation target)
0x00200000              — Actual kernel load address (from FIT)
0x02000000              — kernel_comp_addr_r (decompression area)
0x05400000              — scriptaddr (reused for boot.sel temp buffer)
0x05500000              — pxefile_addr_r
0x05600000              — fdt_addr_r
0x05700000              — ramdisk_addr_r
0x30000000              — fit_addr (FIT image load)
fdt_addr                — Set by Pi firmware (DTB location, varies)
initrd_start/initrd_end — Set by 'bootm ramdisk' (from FIT)
```

## Production Checklist

- [ ] Change `CONFIG_AUTOBOOT_STOP_STR` from `"changeme"` to a real password
- [ ] Verify `uart_2ndstage=0` in EEPROM config
- [ ] Program OTP: `program_pubkey` then `revoke_devkey`
- [ ] Test A/B kernel rollback (trigger snap refresh, verify try/trying/clear cycle)
- [ ] Test boot retry (corrupt FIT, verify auto-reset after 60s)
- [ ] Verify tampered boot.scr on partition is ignored
- [ ] Verify tampered boot.sel with injected vars is rejected by `-v` validation
