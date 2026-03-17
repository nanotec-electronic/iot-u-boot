# RP1 GPIO Patches — Original Reference

Original mbox-format patches for RP1 GPIO support on BCM2712 (Raspberry Pi 5).

## Sources

- **Patches 0001-0002**: Torsten Duwe, from lore.kernel.org u-boot mailing list (RESEND series)
- **Patches 0003-0013**: xen-troops/u-boot `rpi5-2024.04-xt` branch (Oleksii Moisieiev / EPAM)

## Conflict Status Against v2026.01

6 of 13 patches do not apply cleanly to upstream v2026.01 due to
upstream code changes between v2024.04 and v2026.01:

| Patch | Status | Conflict area |
|-------|--------|---------------|
| 0001 | Clean | |
| 0002 | Clean | |
| 0003 | Clean | |
| 0004 | Clean | |
| 0005 | **Conflict** | `arch/arm/mach-bcm283x/Kconfig` |
| 0006 | **Conflict** | `drivers/pci/pcie_brcmstb.c` |
| 0007 | Clean | |
| 0008 | Clean | |
| 0009 | **Conflict** | `drivers/mfd/rp1.c` (depends on 0008) |
| 0010 | **Conflict** | `drivers/gpio/Kconfig` |
| 0011 | **Conflict** | `drivers/pci/pcie_brcmstb.c` |
| 0012 | **Conflict** | `drivers/pci/pcie_brcmstb.c` |
| 0013 | **Conflict** | `board/raspberrypi/rpi/rpi.c` |

## Authoritative Source

The branch commits on `tpm-RP1-BCM2712-poc` are the conflict-resolved,
tested versions. These patch files are preserved as reference only.
