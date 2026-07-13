"""Show next version (patch bump) for a package — mirrors the Rust bump module."""

import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Optional

from .debver import compare_versions

PACKAGES_INDEX_URL = "https://cardputerzero.github.io/packages/dists/stable/main/binary-arm64/Packages"


def run(deb: Optional[str] = None):
    deb_path = resolve_deb(deb)

    package = dpkg_field(deb_path, "Package")
    current_ver = dpkg_field(deb_path, "Version")

    print(f"Package: {package}")
    print(f"Current version in deb: {current_ver}")

    latest = fetch_latest_version(package)
    if latest:
        print(f"Latest published version: {latest}")
        next_ver = bump_patch(latest)
    else:
        print("No published version found (new package)")
        next_ver = bump_patch(current_ver)

    print()
    print(f"Next version: {next_ver}")
    print()
    print("To rebuild with this version, update your package control file:")
    print(f"  Version: {next_ver}")
    print()
    print("Or if using CMake/packaging scripts, set:")
    print(f"  PKG_VERSION={next_ver}")


def resolve_deb(deb: Optional[str]) -> str:
    if deb:
        if not Path(deb).is_file():
            print(f"file not found: {deb}", file=sys.stderr)
            sys.exit(1)
        return deb
    build_dir = Path("build")
    if build_dir.is_dir():
        debs = list(build_dir.glob("*.deb"))
        if len(debs) == 1:
            return str(debs[0])
        if len(debs) > 1:
            print("multiple .deb files in build/. Specify one with --deb <path>", file=sys.stderr)
            sys.exit(1)
    print("no .deb file found. Specify with --deb <path>", file=sys.stderr)
    sys.exit(1)


def dpkg_field(deb_path: str, field: str) -> str:
    try:
        result = subprocess.run(["dpkg-deb", "-f", deb_path, field],
                                capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        print(f"ERROR: dpkg-deb -f {field} failed", file=sys.stderr)
        sys.exit(1)


def fetch_latest_version(package: str) -> Optional[str]:
    try:
        resp = urllib.request.urlopen(PACKAGES_INDEX_URL, timeout=10)
        content = resp.read().decode()
    except Exception:
        return None

    in_package = False
    latest = None

    for line in content.splitlines():
        if line.startswith("Package: "):
            in_package = (line[len("Package: "):] == package)
        if in_package and line.startswith("Version: "):
            ver = line[len("Version: "):]
            if latest is None or compare_versions(ver, latest) > 0:
                latest = ver
        if line == "":
            in_package = False

    return latest


def bump_patch(version: str) -> str:
    base = version.split("-")[0]
    parts = base.split(".")
    if len(parts) == 1:
        return f"{parts[0]}.0.1"
    elif len(parts) == 2:
        return f"{parts[0]}.{parts[1]}.1"
    else:
        try:
            patch = int(parts[2])
        except ValueError:
            patch = 0
        return f"{parts[0]}.{parts[1]}.{patch + 1}"
