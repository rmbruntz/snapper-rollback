#!/usr/bin/env -S python3
# -*- coding: utf-8 -*-

"""
Script to rollback to snapper snapshot using the layout proposed in the snapper
archwiki page
https://wiki.archlinux.org/index.php/Snapper#Suggested_filesystem_layout
"""

from datetime import datetime

import argparse
import btrfsutil
import configparser
import logging
import os
import pathlib
import sys


LOG = logging.getLogger()
LOG.setLevel("INFO")
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
ch = logging.StreamHandler()
ch.setFormatter(formatter)
LOG.addHandler(ch)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Rollback to snapper snapshot based on snapshot ID",
    )
    parser.add_argument(
        "snap_id", metavar="SNAPID", type=str, help="ID of snapper snapshot"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="don't actually do anything, just print the actions out",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=str,
        default="/etc/snapper-rollback.conf",
        help="configuration file to use (default: /etc/snapper-rollback.conf)",
    )

    parser.add_argument(
        "-s",
        "--section",
        type=str,
        default="root",
        help="configuration section to use (default: root)",
    )
    args = parser.parse_args()
    return args


def read_config(configfile):
    config = configparser.ConfigParser()
    config.read(configfile)
    return config


def ensure_dir(dirpath, dry_run=False):
    if not os.path.isdir(dirpath):
        try:
            if dry_run:
                LOG.info("mkdir -p '{}'".format(dirpath))
            else:
                os.makedirs(dirpath)
        except OSError as e:
            LOG.fatal("error creating dir '{}': {}".format(dirpath, e))
            raise


def mount_subvol_id5(target, source=None, dry_run=False):
    """
    There is no built-in `mount` function in python, let's shell out to an `os.system` call
    Also see https://stackoverflow.com/a/29156997 for a cleaner alternative
    """

    ensure_dir(target, dry_run=dry_run)

    if not os.path.ismount(target):
        shellcmd = "mount -o subvolid=5 {} {}".format(source or "", target)
        if dry_run:
            LOG.info(shellcmd)
            ret = 0
        else:
            ret = os.system(shellcmd)
        if ret != 0:
            raise OSError("unable to mount {}".format(target))


def unmount_subvol_id5(target, dry_run=False):
    """
    Unmount the subvolume with ID 5
    """
    if not os.path.ismount(target) and not dry_run:
        LOG.warning("Not mounted: {}".format(target))
        return

    shellcmd = "umount {}".format(target)
    if dry_run:
        LOG.info(shellcmd)
        ret = 0
    else:
        ret = os.system(shellcmd)
    if ret != 0:
        raise OSError("unable to unmount {}".format(target))


def rollback(subvol_main, subvol_main_newname, subvol_rollback_src, dev, set_default_subvol, dry_run=False):
    """
    Rename linux root subvolume, then create a snapshot of the subvolume to
    the old linux root location
    """
    try:
        if dry_run:
            LOG.info("mv {} {}".format(subvol_main, subvol_main_newname))
            LOG.info(
                "btrfs subvolume snapshot {} {}".format(
                    subvol_rollback_src, subvol_main
                )
            )
            if set_default_subvol:
                LOG.info("btrfs subvolume set-default {}".format(subvol_main))
        else:
            os.rename(subvol_main, subvol_main_newname)
            btrfsutil.create_snapshot(subvol_rollback_src, subvol_main)
            if set_default_subvol:
                btrfsutil.set_default_subvolume(subvol_main)
        LOG.info(
            "{}Rollback to {} complete. Reboot to finish".format(
                "[DRY-RUN MODE] " if dry_run else "", subvol_rollback_src
            )
        )
    except FileNotFoundError as e:
        LOG.fatal(
            f"Missing {subvol_main}: Is {dev} mounted with the option subvolid=5?"
        )
    except btrfsutil.BtrfsUtilError as e:
        # Handle errors from btrfs utilities
        LOG.error(f"{e}")
        # Restore old linux root if btrfs utilities fail
        if not os.path.isdir(subvol_main):
            LOG.info(f"Moving {subvol_main_newname} back to {subvol_main}")
            if dry_run:
                LOG.warning("mv {} {}".format(subvol_main_newname, subvol_main))
            else:
                os.rename(subvol_main_newname, subvol_main)


def main():
    args = parse_args()
    config = read_config(args.config)
    section = args.section

    if not config.has_section(section):
        LOG.fatal(f"Missing config section: {section}")
        sys.exit(1)

    mountpoint = pathlib.Path(config.get(section, "mountpoint"))
    subvol_main = mountpoint / config.get(section, "subvol_main")
    subvol_rollback_src = (
        mountpoint / config.get(section, "subvol_snapshots") / args.snap_id / "snapshot"
    )
    try:
        dev = config.get(section, "dev")
    except configparser.NoOptionError as e:
        dev = None

    try:
        set_default_subvol = config.getboolean(section, "set_default_subvol")
    except configparser.NoOptionError:
        set_default_subvol = True

    confirm_typed_value = "CONFIRM"
    try:
        confirmation = input(
            f"Are you SURE you want to rollback? Type '{confirm_typed_value}' to continue: "
        )
        if confirmation != confirm_typed_value:
            LOG.fatal("Bad confirmation, exiting...")
            sys.exit(0)
    except KeyboardInterrupt as e:
        sys.exit(1)

    date = datetime.now().strftime("%Y-%m-%dT%H:%M")
    subvol_main_newname = pathlib.Path(f"{subvol_main}{date}")
    try:
        mount_subvol_id5(mountpoint, source=dev, dry_run=args.dry_run)
        rollback(
            subvol_main,
            subvol_main_newname,
            subvol_rollback_src,
            dev,
            set_default_subvol,
            dry_run=args.dry_run,
        )
        if config.getboolean(section, "unmount_btrfs_root", fallback=False):
            unmount_subvol_id5(mountpoint, dry_run=args.dry_run)
    except PermissionError as e:
        LOG.fatal("Permission denied: {}".format(e))
        exit(1)


if __name__ == "__main__":
    main()
