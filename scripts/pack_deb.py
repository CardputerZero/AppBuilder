#!/usr/bin/env python3
"""
Generic DEB packaging script for M5CardputerZero APPLaunch apps.

Follows the packaging conventions from:
  M5CardputerZero-UserDemo/doc/APPLaunch-App-打包指南.md
"""
import argparse
import glob
import os
import shutil
import subprocess
from datetime import datetime


INSTALL_PREFIX = 'usr/share/APPLaunch'
BIN_PATH       = f'{INSTALL_PREFIX}/bin'
LIB_PATH       = f'{INSTALL_PREFIX}/lib'
SHARE_PATH     = f'{INSTALL_PREFIX}/share'
APP_PATH       = f'{INSTALL_PREFIX}/applications'
SERVICE_PATH   = 'lib/systemd/system'


def build_deb(package_name, version, bin_name, app_name, src_folder, output_dir,
              revision='m5stack1', with_service=True, service_user='pi'):
    staging = os.path.join(output_dir, f'debian-{package_name}')
    deb_file = os.path.join(output_dir,
                            f'{package_name}_{version}-{revision}_arm64.deb')

    if os.path.exists(staging):
        shutil.rmtree(staging)
    os.makedirs(output_dir, exist_ok=True)

    for d in [
        os.path.join(staging, 'DEBIAN'),
        os.path.join(staging, BIN_PATH),
        os.path.join(staging, LIB_PATH),
        os.path.join(staging, SHARE_PATH, 'font'),
        os.path.join(staging, SHARE_PATH, 'images'),
        os.path.join(staging, APP_PATH),
        os.path.join(staging, SERVICE_PATH),
    ]:
        os.makedirs(d, exist_ok=True)

    # --- binary ---
    bin_src = os.path.join(src_folder, bin_name)
    if not os.path.exists(bin_src):
        bin_src = os.path.join(src_folder, 'bin', bin_name)
    if not os.path.exists(bin_src):
        raise FileNotFoundError(f'Binary {bin_name} not found in {src_folder}')
    shutil.copy2(bin_src, os.path.join(staging, BIN_PATH, bin_name))

    # --- fonts ---
    for f in glob.glob(os.path.join(src_folder, '**', '*.ttf'), recursive=True):
        shutil.copy2(f, os.path.join(staging, SHARE_PATH, 'font'))

    # --- images ---
    for f in glob.glob(os.path.join(src_folder, '**', '*.png'), recursive=True):
        shutil.copy2(f, os.path.join(staging, SHARE_PATH, 'images'))

    # --- .desktop entry ---
    icon = 'share/images/email.png'
    pngs = glob.glob(os.path.join(staging, SHARE_PATH, 'images', '*.png'))
    if pngs:
        icon = f'share/images/{os.path.basename(pngs[0])}'

    desktop = os.path.join(staging, APP_PATH, f'{package_name}.desktop')
    with open(desktop, 'w') as f:
        f.write('[Desktop Entry]\n')
        f.write(f'Name={app_name}\n')
        f.write(f'Exec=/{BIN_PATH}/{bin_name}\n')
        f.write('Terminal=false\n')
        f.write(f'Icon={icon}\n')
        f.write('Type=Application\n')

    # --- DEBIAN/control ---
    with open(os.path.join(staging, 'DEBIAN', 'control'), 'w') as f:
        f.write(f'Package: {package_name}\n')
        f.write(f'Version: {version}\n')
        f.write('Architecture: arm64\n')
        f.write('Maintainer: m5stack <m5stack@m5stack.com>\n')
        f.write(f'Section: APPLaunch\n')
        f.write('Priority: optional\n')
        f.write('Homepage: https://www.m5stack.com\n')
        f.write(f'Packaged-Date: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n')
        f.write(f'Description: M5CardputerZero {app_name}\n')

    # --- DEBIAN/postinst ---
    service_name = f'{package_name}.service'
    with open(os.path.join(staging, 'DEBIAN', 'postinst'), 'w') as f:
        f.write('#!/bin/sh\n')
        if with_service:
            f.write(f'[ -f "/lib/systemd/system/{service_name}" ] && systemctl enable {service_name}\n')
            f.write(f'[ -f "/lib/systemd/system/{service_name}" ] && systemctl start {service_name}\n')
        f.write('exit 0\n')

    # --- DEBIAN/prerm ---
    with open(os.path.join(staging, 'DEBIAN', 'prerm'), 'w') as f:
        f.write('#!/bin/sh\n')
        if with_service:
            f.write(f'[ -f "/lib/systemd/system/{service_name}" ] && systemctl stop {service_name}\n')
            f.write(f'[ -f "/lib/systemd/system/{service_name}" ] && systemctl disable {service_name}\n')
        f.write('exit 0\n')

    # --- systemd service ---
    if with_service:
        with open(os.path.join(staging, SERVICE_PATH, service_name), 'w') as f:
            f.write('[Unit]\n')
            f.write(f'Description={app_name} Service\n\n')
            f.write('[Service]\n')
            # AppStore policy: apps must NOT run as root. Pin the service to an
            # unprivileged user (defaults to the launcher's run user). Packages
            # whose services run as root are rejected by czdev and CI.
            if service_user and service_user not in ('root', '0'):
                f.write(f'User={service_user}\n')
                f.write(f'Group={service_user}\n')
            f.write(f'ExecStart=/{BIN_PATH}/{bin_name}\n')
            f.write(f'WorkingDirectory=/{INSTALL_PREFIX}\n')
            f.write('Restart=always\n')
            f.write('RestartSec=1\n')
            f.write('StartLimitInterval=0\n\n')
            f.write('[Install]\n')
            f.write('WantedBy=multi-user.target\n')

    # --- permissions ---
    os.chmod(os.path.join(staging, 'DEBIAN', 'postinst'), 0o755)
    os.chmod(os.path.join(staging, 'DEBIAN', 'prerm'), 0o755)
    os.chmod(os.path.join(staging, BIN_PATH, bin_name), 0o755)

    # --- build .deb ---
    subprocess.run(['dpkg-deb', '-b', staging, deb_file], check=True)
    print(f'Created: {deb_file}')

    shutil.rmtree(staging)
    return deb_file


def main():
    parser = argparse.ArgumentParser(description='Build DEB for M5CardputerZero APPLaunch')
    parser.add_argument('--package-name', required=True, help='Package name (lowercase)')
    parser.add_argument('--version', default='0.1', help='Package version')
    parser.add_argument('--bin-name', required=True, help='Executable file name')
    parser.add_argument('--app-name', default='', help='Display name in launcher')
    parser.add_argument('--src-folder', required=True, help='Path to dist/ folder')
    parser.add_argument('--output-dir', default='.', help='Output directory for .deb')
    parser.add_argument('--revision', default='m5stack1', help='Package revision')
    parser.add_argument('--no-service', action='store_true', help='Skip systemd service generation')
    parser.add_argument('--service-user', default='pi',
                        help='Non-root user the systemd service runs as (default: pi). '
                             'Apps must not run as root; root services are rejected by czdev/CI.')
    args = parser.parse_args()

    if not args.app_name:
        args.app_name = args.package_name

    build_deb(
        package_name=args.package_name,
        version=args.version,
        bin_name=args.bin_name,
        app_name=args.app_name,
        src_folder=args.src_folder,
        output_dir=args.output_dir,
        revision=args.revision,
        with_service=not args.no_service,
        service_user=args.service_user,
    )


if __name__ == '__main__':
    main()
