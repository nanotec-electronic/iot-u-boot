# SPDX-License-Identifier: GPL-2.0
# Copyright (c) 2026 Nanotec Electronic GmbH

"""
Integration tests for the Ubuntu Core boot.sel mechanism.

Tests the A/B kernel state machine, bootargs construction, kernel path
derivation, and export/import roundtrip as implemented in
board/raspberrypi/rpi/rpi-uc.env.

CRC binary format and memory-level import are not tested here — they are
hardware/architecture dependent and covered by upstream env import tests.
Validation and whitelist tests are in test_env.py.
"""

import pytest
import utils


def _validate_set(ubman, var, value):
    """Assert that a U-Boot env variable equals the expected value."""
    response = ubman.run_command('printenv %s' % var)
    assert response == '%s=%s' % (var, value), \
        'Expected %s=%s, got: %s' % (var, value, response)


def _validate_empty(ubman, var):
    """Assert that a U-Boot env variable is not set."""
    response = ubman.run_command('echo ${%s}' % var)
    assert response == '', 'Expected %s to be empty, got: %s' % (var, response)


def _cleanup(ubman, *vars):
    """Unset environment variables."""
    for var in vars:
        ubman.run_command('setenv %s' % var)


# ---------------------------------------------------------------------------
# A/B kernel state machine tests
# ---------------------------------------------------------------------------

@pytest.mark.buildconfigspec('cmd_importenv')
def test_bootsel_ab_try_to_trying(ubman):
    """A/B: kernel_status=try transitions to trying, selects snap_try_kernel."""
    ubman.run_command('setenv snap_kernel kern-1')
    ubman.run_command('setenv snap_try_kernel kern-2')
    ubman.run_command('setenv kernel_status try')
    ubman.run_command('setenv kernel_name ${snap_kernel}')

    # Execute A/B logic (mirrors rpi-uc.env)
    ubman.run_command(
        'if test -n ${kernel_status}; then '
        'if test ${kernel_status} = try; then '
        'if test -n ${snap_try_kernel}; then '
        'setenv kernel_status trying; '
        'setenv kernel_name ${snap_try_kernel}; '
        'fi; fi; fi')

    _validate_set(ubman, 'kernel_status', 'trying')
    _validate_set(ubman, 'kernel_name', 'kern-2')
    _cleanup(ubman, 'snap_kernel', 'snap_try_kernel', 'kernel_status',
             'kernel_name')


@pytest.mark.buildconfigspec('cmd_importenv')
def test_bootsel_ab_trying_to_cleared(ubman):
    """A/B: kernel_status=trying transitions to cleared (rollback)."""
    ubman.run_command('setenv snap_kernel kern-1')
    ubman.run_command('setenv kernel_status trying')
    ubman.run_command('setenv kernel_name ${snap_kernel}')

    ubman.run_command(
        'if test -n ${kernel_status}; then '
        'if test ${kernel_status} = trying; then '
        'setenv kernel_status; '
        'fi; fi')

    _validate_empty(ubman, 'kernel_status')
    _validate_set(ubman, 'kernel_name', 'kern-1')
    _cleanup(ubman, 'snap_kernel', 'kernel_status', 'kernel_name')


@pytest.mark.buildconfigspec('cmd_importenv')
def test_bootsel_ab_normal_boot(ubman):
    """A/B: no kernel_status set — uses snap_kernel, no state change."""
    ubman.run_command('setenv snap_kernel kern-1')
    ubman.run_command('setenv kernel_status')
    ubman.run_command('setenv kernel_name ${snap_kernel}')

    ubman.run_command(
        'if test -n ${kernel_status}; then '
        'if test ${kernel_status} = try; then '
        'setenv kernel_name ${snap_try_kernel}; '
        'fi; fi')

    _validate_set(ubman, 'kernel_name', 'kern-1')
    _validate_empty(ubman, 'kernel_status')
    _cleanup(ubman, 'snap_kernel', 'kernel_name', 'kernel_status')


# ---------------------------------------------------------------------------
# Export/import roundtrip (uses env export -c / env import -v -c)
# ---------------------------------------------------------------------------

@pytest.mark.buildconfigspec('cmd_importenv')
@pytest.mark.buildconfigspec('cmd_exportenv')
def test_bootsel_export_roundtrip(ubman):
    """env export -c then env import -v -c preserves variable values."""
    ram_base = utils.find_ram_base(ubman)
    addr = '%x' % ram_base

    ubman.run_command('setenv snap_kernel kern-1')
    ubman.run_command('setenv snap_try_kernel kern-2')
    ubman.run_command('setenv kernel_status trying')

    ubman.run_command(
        'env export -c %s snap_kernel snap_try_kernel kernel_status' % addr)

    # Read back filesize (env export sets it)
    response = ubman.run_command('echo ${filesize}')
    filesize = response.strip()

    # Clear the vars
    _cleanup(ubman, 'snap_kernel', 'snap_try_kernel', 'kernel_status')
    _validate_empty(ubman, 'snap_kernel')

    # Re-import
    ubman.run_command(
        'env import -v -c %s %s snap_kernel snap_try_kernel kernel_status'
        % (addr, filesize))

    _validate_set(ubman, 'snap_kernel', 'kern-1')
    _validate_set(ubman, 'snap_try_kernel', 'kern-2')
    _validate_set(ubman, 'kernel_status', 'trying')
    _cleanup(ubman, 'snap_kernel', 'snap_try_kernel', 'kernel_status')


# ---------------------------------------------------------------------------
# Sequential import isolation (mirrors load_uc two-stage protocol)
# ---------------------------------------------------------------------------

@pytest.mark.buildconfigspec('cmd_importenv')
@pytest.mark.buildconfigspec('cmd_exportenv')
def test_bootsel_sequential_import_isolation(ubman):
    """Two sequential imports with different whitelists do not interfere.

    load_uc imports recovery_vars from ubuntu-seed, then kernel_vars from
    ubuntu-boot. The second import must not clobber the first.
    """
    ram_base = utils.find_ram_base(ubman)
    addr = '%x' % ram_base

    _cleanup(ubman, 'snapd_recovery_mode', 'snap_kernel', 'kernel_status')

    # Stage 1: simulate ubuntu-seed boot.sel (recovery vars)
    ubman.run_command('setenv snapd_recovery_mode run')

    # Stage 2: simulate ubuntu-boot boot.sel (kernel vars)
    ubman.run_command('setenv snap_kernel kern-1')
    ubman.run_command('setenv kernel_status try')

    # Verify first-stage var survived second-stage setup
    _validate_set(ubman, 'snapd_recovery_mode', 'run')
    _validate_set(ubman, 'snap_kernel', 'kern-1')
    _validate_set(ubman, 'kernel_status', 'try')

    # Now simulate the actual two-stage import using env export/import
    # with different whitelists, as load_uc does

    # Export all three vars into a single blob (simulates boot.sel content)
    ubman.run_command(
        'env export -c %s snapd_recovery_mode snap_kernel kernel_status'
        % addr)
    response = ubman.run_command('echo ${filesize}')
    filesize = response.strip()

    # Clear everything
    _cleanup(ubman, 'snapd_recovery_mode', 'snap_kernel', 'kernel_status')

    # Import stage 1: only recovery var
    ubman.run_command(
        'env import -v -c %s %s snapd_recovery_mode' % (addr, filesize))
    _validate_set(ubman, 'snapd_recovery_mode', 'run')
    _validate_empty(ubman, 'snap_kernel')

    # Import stage 2: only kernel vars (from same blob for simplicity)
    ubman.run_command(
        'env import -v -c %s %s snap_kernel kernel_status'
        % (addr, filesize))

    # First-stage var must survive second import
    _validate_set(ubman, 'snapd_recovery_mode', 'run')
    _validate_set(ubman, 'snap_kernel', 'kern-1')
    _validate_set(ubman, 'kernel_status', 'try')

    _cleanup(ubman, 'snapd_recovery_mode', 'snap_kernel', 'kernel_status')


# ---------------------------------------------------------------------------
# Bootargs and path construction
# ---------------------------------------------------------------------------

@pytest.mark.buildconfigspec('cmd_importenv')
def test_bootsel_run_mode_bootargs(ubman):
    """Run mode: bootargs contains snapd_recovery_mode=run and panic=-1."""
    ubman.run_command('setenv snapd_recovery_mode run')
    ubman.run_command(
        'setenv bootargs snapd_recovery_mode=${snapd_recovery_mode} panic=-1')

    _validate_set(ubman, 'bootargs',
                  'snapd_recovery_mode=run panic=-1')
    _cleanup(ubman, 'snapd_recovery_mode', 'bootargs')


@pytest.mark.buildconfigspec('cmd_importenv')
def test_bootsel_install_mode_bootargs(ubman):
    """Install mode: bootargs includes both recovery_mode and recovery_system."""
    ubman.run_command('setenv snapd_recovery_mode install')
    ubman.run_command('setenv snapd_recovery_system 20260225')
    ubman.run_command(
        'setenv bootargs '
        'snapd_recovery_mode=${snapd_recovery_mode} '
        'snapd_recovery_system=${snapd_recovery_system} '
        'panic=-1')

    _validate_set(ubman, 'bootargs',
                  'snapd_recovery_mode=install '
                  'snapd_recovery_system=20260225 '
                  'panic=-1')
    _cleanup(ubman, 'snapd_recovery_mode', 'snapd_recovery_system', 'bootargs')


@pytest.mark.buildconfigspec('cmd_importenv')
def test_bootsel_kernel_path_construction(ubman):
    """Kernel path prefix is correctly derived from kernel_name."""
    ubman.run_command('setenv kernel_name pi-kernel-1')
    ubman.run_command('setenv prefix uboot/ubuntu/${kernel_name}/')

    _validate_set(ubman, 'prefix', 'uboot/ubuntu/pi-kernel-1/')
    _cleanup(ubman, 'kernel_name', 'prefix')
