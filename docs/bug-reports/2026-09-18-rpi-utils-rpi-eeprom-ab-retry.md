---
target: raspberrypi/utils (rpi-eeprom-ab)
channel: ""
tracker: ""
status: not reported
reported: ""
affected: "rpi-eeprom-ab from raspberrypi/utils at ebc4a56bac3a896d5c14e56fe27dcd6cb36dd373; CM5 / BCM2712, Ubuntu Core 26, kernel 7.0"
fixed_in: ""
workaround: "coherent_pool=16M on the kernel command line (package coherent-pool-for-ab-firmware-update): lowers the probability of the transient failure, does not bound it."
package: report-rpi-eeprom-ab-retry-upstream
last_checked: 2026-11-04
---

# rpi-eeprom-ab: a transient host-side allocation failure aborts the whole update

Status: **written up, not yet sent.** Prepared 2026-09-18 for
`raspberrypi/utils`. Everything below was measured on our own hardware or read
from the pinned vendor source; nothing is inferred from a changelog.

Related: `iot-u-boot` package `coherent-pool-for-ab-firmware-update`, and the
kernel-cmdline comment in `board/raspberrypi/rpi/rpi-uc.env`.

## Summary

`rpi_eeprom_ab_write_eeprom_update` retries a packet only when the firmware
answers `BUSY`. A **host-side** failure — the kernel refusing the mailbox
ioctl because the atomic DMA pool cannot satisfy the 256 KiB allocation —
arrives as `RPI_EEPROM_AB_ERROR_FAILED` and aborts the entire update on the
first occurrence.

That condition is transient and self-healing: the pool refills asynchronously,
so the same packet would very likely succeed milliseconds later. The retry
budget that would cover it already exists and is simply not wired to this class
of error.

Effect in the field: bootloader updates fail non-deterministically. A device
that *sometimes* takes a security update is worse than one that clearly cannot,
because nothing signals that the update is being missed.

## Environment

- CM5 / BCM2712, 8 GB, Ubuntu Core 26, kernel 7.0 (Canonical `linux-raspi`)
- `rpi-eeprom-ab` built from `raspberrypi/utils` at `ebc4a56bac3a896d5c14e56fe27dcd6cb36dd373`
- Bootloader `pieeprom-2026-09-12` (BUILD_TIMESTAMP 1789171628)
- EEPROM A/B-partitioned, `spi-check: OK`, `dtoverlay=nospi10` in config.txt

## What we observed

`rpi-eeprom-ab update <partition image>` failed, printing a single progress dot
— i.e. it aborted on the first packet:

```
$ sudo rpi-eeprom-ab update pieeprom-2026-06-17-ab.bin
ioctl_mbox_property failed: -1
file_size: 1011712
.
Failed to write update: Unknown error. Please check you are running a firmware version that supports AB.
(rc 255)
```

The suggested cause in that message is misleading: A/B is supported and working
on this device — `partition-status` and `update-status` answer correctly before
and after, and `partition-status` is byte-identical across the failure, so
nothing was written.

`dmesg` at the moment of the failure names the real one:

```
DMA pool exhausted for 107c013880.mailbox
WARNING: kernel/dma/pool.c:291 at dma_alloc_from_pool+0x164/0x178, CPU#1: rpi-eeprom-ab/1440
 dma_alloc_from_pool+0x164/0x178 (P)
 dma_direct_alloc_from_pool+0xc0/0x1e0
 dma_direct_alloc+0xe4/0x3f0
 dma_alloc_attrs+0x94/0x390
 vcio_user_property_list+0xac/0x250
 vcio_device_ioctl+0x34/0x90
```

It is not CMA — `CmaTotal 65536 kB / CmaFree 64936 kB`, essentially untouched.
It is the kernel's *atomic* DMA pool, which on this board is:

```
[    0.011353] DMA: preallocated 512 KiB GFP_KERNEL|GFP_DMA pool for atomic allocations

/sys/kernel/debug/dma_pools/pool_size_dma     524288
/sys/kernel/debug/dma_pools/pool_size_kernel       0
/sys/kernel/debug/dma_pools/pool_size_dma32        0
```

512 KiB against a 256 KiB allocation per packet: room for two, and the
allocator (`gen_pool_first_fit_order_align`) needs a **256 KiB-aligned**
contiguous run, so free bytes do not predict success.

The failure is not deterministic. An earlier run on the same device wrote
several packets successfully and failed on a later one; subsequent runs failed
on the first packet, on a freshly booted idle device, with both an older and a
byte-identical image.

## Where it goes wrong in the code

`rpieepromab.c:277-281` — a failed ioctl becomes `FAILED`, which is
deliberately distinct from a firmware-reported error:

```c
rc = mbox_property(mb, (struct firmware_msg *)&msg);
mbox_close(mb);

if (rc < 0) {
    LOG_DEBUG("Mailbox property failed: %d\n", rc);
    return RPI_EEPROM_AB_ERROR_FAILED;
}
if (msg.error & VC_MAILBOX_ERROR) {
    return (RPI_EEPROM_AB_ERROR)(msg.error & ~VC_MAILBOX_ERROR);
}
```

`rpieepromab.c:646-665` — the retry loop treats that as fatal:

```c
for (attempt = 0; attempt < 7; attempt++) {
    err = rpi_eeprom_ab_update_packet_write(sent_len, message, packet_length);
    ...
    if (err == RPI_EEPROM_AB_ERROR_NO_ERROR) {
        break;
    } else if (err == RPI_EEPROM_AB_ERROR_BUSY) {
        LOG_DEBUG("EEPROM is busy, retrying...\n");
        continue;
    } else if (err != RPI_EEPROM_AB_ERROR_NO_ERROR) {
        LOG_DEBUG("Failed to send to EEPROM: %s\n", ...);
        return err;          /* <-- aborts the whole update */
    }
}
```

So the distinction the code already draws — host-side `rc < 0` versus a
firmware verdict in `msg.error` — is made at the transport layer and then
discarded at the retry layer.

## Suggested fix

Retry on a host-side transport failure as well, ideally with a short delay so
the pool's async refill (`atomic_pool_work_fn`, `kernel/dma/pool.c:151-160`) has
a chance to run:

```c
} else if (err == RPI_EEPROM_AB_ERROR_BUSY ||
           err == RPI_EEPROM_AB_ERROR_FAILED) {
    continue;
}
```

A firmware-reported error still aborts immediately, which is right — it is a
verdict about the device. `FAILED` is not; it means the message never reached
the firmware.

Secondary, and arguably the proper fix: `rpi_firmware_property_list` allocates
with `GFP_ATOMIC` (`drivers/firmware/raspberrypi.c`), but the `vcio` path
reaching it is process context and demonstrably sleepable — `vcio.c` does a
`kmalloc(size, GFP_KERNEL)` immediately before, and
`rpi_firmware_property_list` then blocks on `wait_for_completion_timeout` under
a mutex. A `GFP_KERNEL` flavour used from `vcio` would avoid the atomic pool
entirely for this caller. We have not audited the other callers of that
exported symbol, so we are not proposing it as a patch.

## Note on pool sizing

Raspberry Pi's own device tree sets `coherent_pool=1M`
(`arch/arm64/boot/dts/broadcom/bcm2712-rpi.dtsi`, the `chosen` node) — four
packets' worth. On a stock Raspberry Pi OS boot the tool therefore runs against
an even smaller pool than the 512 KiB we measured, so this is unlikely to be
specific to our configuration.

## Our mitigation, and why it is not a fix

We set `coherent_pool=16M` on the kernel command line. Measured effect on the
same device: `pool_size_dma` 20971520 (~40x), two consecutive updates completed
18/18 packets with `update-status: Success`, and zero `DMA pool exhausted` lines
across the boot — against two per boot before.

That lowers the probability of hitting fragmentation. It does not bound it: the
allocation can still fail under memory pressure, and the tool will still abort
on the first occurrence. The retry is what would make the operation robust.

## Status log

- 2026-09-18: measured and written up; mitigated in this repo by `coherent_pool=16M` (package `coherent-pool-for-ab-firmware-update`).
- 2026-11-04: documented, not reported for now (decision 2026-11-04).
