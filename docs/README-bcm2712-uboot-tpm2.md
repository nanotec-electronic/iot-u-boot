# TPM 2.0 over SoftSPI on BCM2712 (Raspberry Pi 5 / CM5)

Adding TPM 2.0 support to U-Boot on the Raspberry Pi 5 / CM5 platform using
GPIO bit-banged SPI (SoftSPI). This documents the approach, rationale,
verification steps, pitfalls, and known limitations.

## Why SoftSPI?

The Raspberry Pi 5 introduced a fundamentally different hardware architecture
compared to Pi 4. The SPI controllers (along with GPIO, I2C, UART, etc.) now
reside inside the **RP1 southbridge**, connected to the BCM2712 SoC via PCIe.

```
BCM2712 SoC ──PCIe──▶ RP1 Southbridge ──▶ SPI0, GPIO, I2C, etc.
```

Upstream U-Boot (as of 2025.07) has **no driver support for RP1 hardware SPI
controllers**. The RP1 SPI peripheral sits behind a PCI-enumerated MFD device
and requires a full driver stack that does not exist yet:

```
PCI bus → RP1 MFD → RP1 SPI controller driver (missing)
```

However, **RP1 GPIO** support has been implemented via a set of community
patches (xen-troops fork). Since U-Boot includes a `soft-spi` driver that
bit-bangs SPI over GPIO pins, we can use this path to communicate with SPI
peripherals — including TPM chips — from U-Boot.

```
PCI bus → RP1 MFD → RP1 GPIO → SoftSPI (bit-bang) → TPM
```

This is a pragmatic workaround validated by reference implementations on the
Pi 4 platform (joholl/rpi4-uboot-tpm, wxleong/tpm2-uboot-rpi4), adapted here
for the Pi 5's PCIe-based architecture.

## Prerequisites

### U-Boot Patches

The following patch sets must be applied to U-Boot (on top of upstream
`rpi_arm64_defconfig` or our `rpi_sb_uc_defconfig`):

1. **RP1 GPIO support** (13 patches from xen-troops/u-boot fork)
   - PCI enumeration of RP1 MFD
   - RP1 GPIO driver (`drivers/gpio/rpi_rp1_gpio.c`)
   - RP1 pinctrl support

2. **env import -v validation** (3 patches from NXP iot-field, optional,
   for secure boot flow)

### U-Boot Config Additions

The following Kconfig options must be enabled beyond the base defconfig:

| Config | Purpose |
|--------|---------|
| `CONFIG_MFD_RP1` | RP1 multi-function device |
| `CONFIG_RP1_GPIO` | RP1 GPIO driver |
| `CONFIG_DM_SPI` | SPI device model |
| `CONFIG_SOFT_SPI` | GPIO bit-bang SPI driver |
| `CONFIG_CMD_SPI` | `sspi` command (debug/verification) |
| `CONFIG_TPM` | TPM subsystem |
| `CONFIG_TPM_V2` | TPM 2.0 protocol |
| `CONFIG_TPM2_TIS_SPI` | TPM TIS SPI transport |
| `CONFIG_CMD_TPM` | `tpm2` commands |
| `CONFIG_CMD_HASH` | Hash commands (optional, for PCR verification) |

### Device Tree Overlay

A DT overlay provides the `spi-gpio` node with the TPM child device. The
overlay uses the `__fixups__` mechanism — the Pi firmware resolves `&gpio`
label references to actual phandle values at boot time when loading overlays
via `config.txt`.

Key points about the overlay:

- Placed at device tree **root** (not inside the RP1 PCI hierarchy)
- Dual GPIO property names: `gpio-sck`/`gpio-mosi`/`gpio-miso` for U-Boot,
  `sck-gpios`/`mosi-gpios`/`miso-gpios` for Linux
- `cs-gpios` is common to both drivers

Activated in `config.txt`:

```ini
dtoverlay=soft-spi-tpm
```

## Pin Mapping

Using the Infineon SLB9672 on a LetsTrust TPM evaluation board
(active on header pins 17–26):

| Signal | GPIO | Header Pin | Note |
|--------|------|------------|------|
| SCLK | 11 | 23 | |
| MOSI | 10 | 19 | |
| MISO | 9 | 21 | |
| CS1 | 7 | 26 | LetsTrust default (active-low) |
| RESET | 24 | 18 | Optional, active-low |

**Important:** The LetsTrust board uses **CS1 (GPIO 7)** by default, not
CS0 (GPIO 8). CS0 remains free for other SPI devices.

## Verification — U-Boot

### 1. Check SoftSPI Bus

```
U-Boot> dm uclass spi
```

Expected: `soft-spi @ seq N` appears in the list.

### 2. Raw SPI Communication (optional, without TPM)

For initial validation, an MCP2518FD CAN controller or similar SPI device
can be used to verify SoftSPI is working before connecting the TPM:

```
U-Boot> sspi <bus>:0.0 48 3E0000000000
```

This reads the MCP2518FD OSC register — expected response: `000060040000`.

### 3. TPM Startup Sequence

```
U-Boot> tpm2 info
U-Boot> tpm2 startup TPM2_SU_CLEAR
U-Boot> tpm2 self_test full
```

- `tpm2 info` — must show VendorID, DeviceID, RevisionID and `[open]`
  or `[closed]`
- `tpm2 startup` / `tpm2 self_test` — no output means success (exit code 0)

**Do NOT run `tpm2 init`** — the TPM2 TIS SPI driver opens the device
during probe. Calling `tpm2 init` afterward returns `-EBUSY (-16)`.
This is expected, not an error.

### 4. Read PCR

```
U-Boot> tpm2 pcr_read 0 0x02000000
U-Boot> md.b 0x02000000 0x20
```

Should return a non-zero SHA-256 hash (Pi firmware writes measurements to
PCR 0 during boot).

### 5. Read Firmware Version

```
U-Boot> tpm2 get_capability 0x6 0x10b 0x02000000 1
U-Boot> md.l 0x02000000 1
```

Decode: upper 16 bits = major, lower 16 bits = minor.
Example: `0x00100018` → FW **16.24**.

## Verification — Linux

After booting into Linux (Debian or Ubuntu Core):

```bash
# Verify device nodes exist
ls -la /dev/tpm0 /dev/tpmrm0

# Install tools
sudo apt install tpm2-tools

# Manufacturer and firmware
tpm2_getcap properties-fixed | grep -A1 -E "TPM2_PT_FIRMWARE_VERSION|TPM2_PT_MANUFACTURER"

# PCR (should match U-Boot reading)
tpm2_pcrread sha256:0

# RNG functional test
tpm2_getrandom 16 | xxd

# Self-test
tpm2_selftest --fulltest && echo "PASS" || echo "FAIL"
```

## Alternative: Two-Stage Overlay (SoftSPI → HW-SPI)

An alternative approach uses SoftSPI only during U-Boot, then switches to
hardware SPI under Linux for better performance.

### Concept

1. **U-Boot phase:** spi-gpio overlay is active (loaded via `config.txt`),
   TPM communicates over bit-banged GPIO
2. **Linux phase:** a second overlay or driver rebind switches the TPM to the
   native RP1 SPI controller

### Why We Did Not Pursue This

- **Pi 5 DTB is firmware-managed.** The firmware delivers the DTB to U-Boot
  with overlays already applied. U-Boot uses this DTB directly
  (`CONFIG_OF_BOARD=y`). There is no clean mechanism to apply one overlay
  for U-Boot and a different one for Linux.
- **Runtime FDT patching** (disable spi-gpio, enable hw-spi before `booti`)
  is fragile and would require maintaining node paths that may change between
  firmware versions.
- **Both reference implementations** (joholl/rpi4-uboot-tpm,
  wxleong/tpm2-uboot-rpi4) use the same approach: a single spi-gpio overlay
  for both U-Boot and Linux.
- **SPI bus independence:** Other SPI buses (e.g., SPI1 for CAN) are
  unaffected. Only SPI0 pins are used for bit-bang; hardware SPI controllers
  on other buses remain fully functional.

This approach may become viable once upstream U-Boot gains native RP1 SPI
driver support, at which point the spi-gpio overlay would no longer be needed
for either stage.

## Pitfalls and Limitations

### `tpm2 init` Returns -16 (EBUSY)

The TPM2 TIS SPI driver calls `tpm_tis_open()` during device probe. The
TPM is already initialized by the time the U-Boot shell is available.
Running `tpm2 init` attempts to open it again → `-EBUSY`.
**Solution:** Skip `tpm2 init`, proceed directly with `tpm2 startup`.

### Memory Address for `get_capability`

The `tpm2 get_capability` command requires a valid RAM address for output.
On BCM2712, **not all addresses are mapped** — using an invalid address
(e.g., `0x40000000`) causes a Synchronous Abort and CPU reset.
**Solution:** Use `0x02000000` or check available ranges with `bdinfo`.

### SoftSPI Performance

Bit-banged SPI is ~10-50x slower than hardware SPI. For TPM operations in
the boot flow (NV counter read, key verification), this adds negligible
latency. However, bulk operations (large key generation, many seal/unseal
cycles) will be noticeably slower.

### GPIO Reset Pin

The TPM driver prints: `TPM gpio reset should not be used on secure
production devices`. For production hardware, the reset pin should be tied
to VCC (always-on) or removed from the design. An attacker with physical
access could otherwise toggle reset to clear PCR values.

### RP1 GPIO Patches Not Upstream

The 13 RP1 GPIO patches are from the xen-troops community fork, not
upstream U-Boot. **Each U-Boot version update requires rebasing these
patches.** This is the primary maintenance burden of this approach.

### `CONFIG_USE_PREBOOT` Required

`PREBOOT` runs `pci enum; usb start;` which initializes the RP1 southbridge
via PCIe. Without it, the RP1 GPIO controller is not available and SoftSPI
will fail to probe. Ensure this is not removed from the defconfig.

### SPI0 Hardware Controller Status

On Pi 5, the hardware `spi@50000` node is `status = "disabled"` in the
firmware DTB by default. Explicitly disabling it in the overlay is
redundant but harmless.

## Tested Hardware

| Component | Details |
|-----------|---------|
| SoC | BCM2712 (Raspberry Pi 5) |
| TPM | Infineon SLB9672, FW 15.24 and FW 16.24 |
| Eval Board | LetsTrust TPM (SLB9672, CS1/GPIO 7) |
| U-Boot | 2025.07-rc2 + RP1 GPIO patches |
| Validation SPI Device | Microchip MCP2518FD (CAN controller, CS0/GPIO 8) |

## References

- [joholl/rpi4-uboot-tpm](https://github.com/joholl/rpi4-uboot-tpm) — Pi 4
  reference using spi-gpio + SLB9670
- [wxleong/tpm2-uboot-rpi4](https://github.com/wxleong/tpm2-uboot-rpi4) —
  Pi 4 reference, same spi-gpio approach
- [xen-troops/u-boot](https://github.com/xen-troops/u-boot) — RP1 GPIO
  patches for BCM2712
- [LetsTrust TPM Documentation](https://letstrust.de) — SLB9672 eval board
  pinout and configuration
- [Infineon SLB9672 FW16 Datasheet](https://www.infineon.com/assets/row/public/documents/30/49/infineon-slb9672-tpm20-spi-fw16.xx-ds-rev1-3-2024-11-18-datasheet-en.pdf)
