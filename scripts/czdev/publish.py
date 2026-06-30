"""Publish a .deb package to the CardputerZero app store — mirrors the Rust publish module."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Optional

from . import auth
from .github_client import GitHubClient, Permission

TARGET_OWNER = "CardputerZero"
TARGET_REPO = "packages"
# Buffer release tag on the push target (contributor fork, or the official repo
# for maintainers) where the .deb is uploaded before review.
BUFFER_RELEASE_TAG = "czdev-buffer"


def run(deb: Optional[str] = None):
    check_git_installed()

    deb_path = resolve_deb(deb)
    print(f"Package: {deb_path}")

    store_meta, app_dir = load_store_meta(deb_path)
    print()

    token = auth.load_token()
    gh = GitHubClient(token)

    user = gh.get_user()

    noreply = f"{user.login}@users.noreply.github.com"
    all_emails = [noreply]
    if user.email:
        all_emails.append(user.email)

    print("Preflight checks:")

    # 1. Check .desktop file exists
    if not check_desktop(deb_path):
        print("ERROR: deb does not contain a .desktop file. All CardputerZero apps must include one.", file=sys.stderr)
        sys.exit(1)
    print("  ✓ .desktop file found")

    # 2. Extract metadata and check email
    meta = extract_metadata(deb_path)
    if not any(e.lower() == meta["maintainer_email"].lower() for e in all_emails):
        print(f"ERROR: Maintainer email does not match your GitHub verified emails.", file=sys.stderr)
        print(f"  Maintainer: {meta['maintainer_email']}", file=sys.stderr)
        print(f"  Your emails: {all_emails}", file=sys.stderr)
        sys.exit(1)
    print("  ✓ Maintainer email matches GitHub account")

    # 3. Package name validation
    if not is_valid_package_name(meta["package"]):
        print(f"ERROR: Invalid package name: '{meta['package']}'", file=sys.stderr)
        sys.exit(1)
    print(f'  ✓ Package name is valid "{meta["package"]}"')

    # 4. Show summary
    file_size = os.path.getsize(deb_path)
    size_mb = file_size / 1_048_576.0
    print(f"  ✓ Version: {meta['version']}, Arch: {meta['architecture']}, Size: {size_mb:.1f} MB")
    print()

    if file_size > 2 * 1024 * 1024 * 1024:
        print(f"ERROR: File too large. GitHub release asset limit is 2 GiB. ({size_mb:.1f} MB)", file=sys.stderr)
        sys.exit(1)

    # 5. Check version is newer than existing
    check_version_newer(meta)

    # Determine target: direct push or fork
    perm = gh.check_permission(TARGET_OWNER, TARGET_REPO, user.login)
    if perm >= Permission.WRITE:
        push_owner = TARGET_OWNER
        push_repo = TARGET_REPO
        pr_head = None
    else:
        print(f"You don't have write access to {TARGET_OWNER}/{TARGET_REPO}.")
        print("  → Forking to your account...  ", end="", flush=True)
        fork_name = gh.fork_repo(TARGET_OWNER, TARGET_REPO)
        print(f"done ({fork_name})")
        parts = fork_name.split("/")
        push_owner = parts[0]
        push_repo = parts[1]
        branch = branch_name(meta)
        pr_head = f"{user.login}:{branch}"

    # Integrity + canonical asset name. GitHub sanitizes '~' to '.' in release
    # asset names, so the on-release/manifest filename may differ from the
    # Debian version string (which keeps '~').
    file_bytes = Path(deb_path).read_bytes()
    sha256_hash = hashlib.sha256(file_bytes).hexdigest()
    file_size = len(file_bytes)
    deb_name = f"{meta['package']}_{meta['version']}_{meta['architecture']}.deb"
    asset_name = deb_name.replace("~", ".")
    manifest_name = f"{asset_name}.release.json"
    manifest_path_in_repo = f"pool/main/{meta['package']}/{manifest_name}"
    branch = branch_name(meta)
    remote_url = f"git@github.com:{push_owner}/{push_repo}.git"

    # 1) Upload the .deb to a buffer Release on the push target. For third-party
    #    contributors this is their own fork — it uses THEIR free Release storage
    #    and bandwidth, so the upstream project stores nothing until the PR is
    #    approved and CI promotes the binary into the official apt-pool release.
    print(f"  → Uploading .deb ({size_mb:.1f} MB) to {push_owner}/{push_repo} release '{BUFFER_RELEASE_TAG}'... ",
          end="", flush=True)
    release = gh.ensure_release(push_owner, push_repo, BUFFER_RELEASE_TAG, name="czdev upload buffer")
    download_url = gh.upload_release_asset(push_owner, push_repo, release, deb_path, asset_name)
    print("done")

    manifest = {
        "filename": asset_name,
        "url": download_url,
        "sha256": sha256_hash,
        "size": file_size,
        "package": meta["package"],
        "version": meta["version"],
        "architecture": meta["architecture"],
    }

    # 2) Commit only metadata (meta.json, screenshots, icon, manifest) to a PR
    #    branch — no .deb, no LFS.
    print(f"Uploading metadata to {TARGET_OWNER}/{TARGET_REPO}...")

    tmp_dir = Path(tempfile.mkdtemp(prefix="czdev-publish-"))
    try:
        run_cmd_in(tmp_dir, ["git", "init"])
        run_cmd_in(tmp_dir, ["git", "remote", "add", "origin", remote_url])

        print("  → git fetch (minimal)... ", end="", flush=True)
        run_cmd_in(tmp_dir, ["git", "fetch", "--depth=1", "--filter=blob:none", "origin", "main"])
        print("done")

        print(f"  → Creating branch {branch}... ", end="", flush=True)
        run_cmd_in(tmp_dir, ["git", "checkout", "-b", branch, "origin/main"])
        print("done")

        dest_dir = tmp_dir / "pool" / "main" / meta["package"]
        dest_dir.mkdir(parents=True, exist_ok=True)

        # Copy screenshots
        if store_meta.get("screenshots"):
            screenshots_dest = dest_dir / "screenshots"
            screenshots_dest.mkdir(parents=True, exist_ok=True)
            for shot in store_meta["screenshots"]:
                src = Path(app_dir) / shot
                if not src.exists():
                    print(f"ERROR: Screenshot not found: {src}", file=sys.stderr)
                    sys.exit(1)
                shutil.copy2(src, screenshots_dest / src.name)

        # Copy icon
        if store_meta.get("icon"):
            icon_src = Path(app_dir) / store_meta["icon"]
            if icon_src.exists():
                shutil.copy2(icon_src, dest_dir / icon_src.name)

        # meta.json + release manifest (the binary stays in the buffer release)
        (dest_dir / "meta.json").write_text(json.dumps(store_meta, indent=2, ensure_ascii=False))
        (dest_dir / manifest_name).write_text(json.dumps(manifest, indent=2) + "\n")

        print("  → Creating commit... ", end="", flush=True)
        run_cmd_in(tmp_dir, ["git", "add", f"pool/main/{meta['package']}"])
        run_cmd_in(tmp_dir, ["git", "commit", "-m",
                             f"publish: {meta['package']} {meta['version']} ({meta['architecture']})"])
        print("done")

        print("  → Pushing branch... ", end="", flush=True)
        run_cmd_in(tmp_dir, ["git", "push", "origin", branch])
        print("done")

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Create PR via API
    head = pr_head if pr_head else branch
    pr_body = (
        f"## Package: `{meta['package']}`\n\n"
        f"| Field | Value |\n"
        f"|-------|-------|\n"
        f"| Version | {meta['version']} |\n"
        f"| Architecture | {meta['architecture']} |\n"
        f"| Maintainer | {meta['maintainer']} |\n"
        f"| Size | {size_mb:.1f} MB |\n"
        f"| SHA-256 | `{sha256_hash}` |\n"
        f"| Manifest | `{manifest_path_in_repo}` |\n"
        f"| Binary | [{asset_name}]({download_url}) |\n\n"
        f"Submitted via `czdev publish`. The `.deb` is hosted on the contributor's "
        f"buffer release; CI verifies the sha256 and, on merge, promotes it into the "
        f"official `apt-pool` release."
    )
    print("  → Creating pull request... ", end="", flush=True)
    pr = gh.create_pull_request(
        TARGET_OWNER, TARGET_REPO,
        f"publish: {meta['package']} {meta['version']}",
        pr_body, head, "main",
    )
    print("done")

    print()
    print("✓ Pull request created:")
    print(f"  {pr.html_url}")
    print()
    print("  The PR will be validated by CI. A maintainer will review and merge it.")


def resolve_deb(deb: Optional[str]) -> str:
    if deb:
        if not os.path.isfile(deb):
            print(f"File not found: {deb}", file=sys.stderr)
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


def load_store_meta(deb_path: str) -> tuple:
    search_dirs = [
        Path.cwd(),
        Path(deb_path).parent,
    ]

    for d in search_dirs:
        manifest_path = d / "app-builder.json"
        if manifest_path.exists():
            raw = json.loads(manifest_path.read_text())

            if "store" not in raw:
                print("app-builder.json found but missing \"store\" section.", file=sys.stderr)
                print('  Add a "store" field with title, summary, categories, screenshots, etc.', file=sys.stderr)
                sys.exit(1)

            store = raw["store"]
            meta = {
                "title": raw.get("app_name", ""),
                "summary": store.get("summary", ""),
                "categories": store.get("categories", []),
                "screenshots": store.get("screenshots", []),
            }
            if store.get("description"):
                meta["description"] = store["description"]
            if store.get("locales"):
                meta["locales"] = store["locales"]
            if store.get("license"):
                meta["license"] = store["license"]
            if store.get("source_repo"):
                meta["source_repo"] = store["source_repo"]
            if store.get("icon"):
                meta["icon"] = store["icon"]
            if store.get("permissions"):
                meta["permissions"] = store["permissions"]

            if not meta["screenshots"]:
                print("No screenshots defined in app-builder.json store.screenshots.", file=sys.stderr)
                print("  Add at least one 320x170 screenshot to publish.", file=sys.stderr)
                sys.exit(1)

            return meta, str(d)

    print("app-builder.json not found in current directory.", file=sys.stderr)
    print("  Run `czdev publish` from your app's project directory.", file=sys.stderr)
    sys.exit(1)


def check_desktop(deb_path: str) -> bool:
    try:
        result = subprocess.run(["dpkg-deb", "-c", deb_path],
                                capture_output=True, text=True, check=True)
        return any(line.endswith(".desktop") for line in result.stdout.splitlines())
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("ERROR: dpkg-deb not found. Is dpkg-deb installed?", file=sys.stderr)
        sys.exit(1)


def extract_metadata(deb_path: str) -> dict:
    fields = ["Package", "Version", "Architecture", "Maintainer"]
    values = {}
    for field in fields:
        try:
            result = subprocess.run(["dpkg-deb", "-f", deb_path, field],
                                    capture_output=True, text=True, check=True)
            values[field] = result.stdout.strip()
        except subprocess.CalledProcessError:
            values[field] = ""

    maintainer = values.get("Maintainer", "")
    email = extract_email(maintainer)

    return {
        "package": values.get("Package", ""),
        "version": values.get("Version", ""),
        "architecture": values.get("Architecture", ""),
        "maintainer": maintainer,
        "maintainer_email": email,
    }


def extract_email(maintainer: str) -> str:
    start = maintainer.find("<")
    end = maintainer.find(">")
    if start != -1 and end != -1:
        return maintainer[start + 1:end]
    return maintainer


def is_valid_package_name(name: str) -> bool:
    if len(name) < 2:
        return False
    valid_chars = set("abcdefghijklmnopqrstuvwxyz0123456789.+-")
    first_chars = set("abcdefghijklmnopqrstuvwxyz0123456789")
    if name[0] not in first_chars:
        return False
    return all(c in valid_chars for c in name)


def branch_name(meta: dict) -> str:
    ts = int(time.time())
    return f"publish/{meta['package']}-{meta['version']}-{ts}"


def check_version_newer(meta: dict):
    packages_url = "https://cardputerzero.github.io/packages/dists/stable/main/binary-arm64/Packages"
    try:
        req = urllib.request.Request(packages_url)
        resp = urllib.request.urlopen(req, timeout=10)
        content = resp.read().decode()
    except Exception:
        return

    in_our_package = False
    existing_version = None

    for line in content.splitlines():
        if line.startswith("Package: "):
            in_our_package = (line[len("Package: "):] == meta["package"])
        if in_our_package and line.startswith("Version: "):
            ver = line[len("Version: "):]
            if existing_version is None or compare_versions(ver, existing_version) > 0:
                existing_version = ver
        if line == "":
            in_our_package = False

    if existing_version:
        if compare_versions(meta["version"], existing_version) <= 0:
            print(f"ERROR: {meta['version']} is not newer than existing version {existing_version}", file=sys.stderr)
            print("  Use `czdev bump` to check the next version.", file=sys.stderr)
            sys.exit(1)
        print(f"  ✓ {meta['version']} is newer than existing {existing_version}")
    else:
        print("  ✓ New package (no existing version found)")


def compare_versions(a: str, b: str) -> int:
    def parse(v):
        return [int(x) for x in re.split(r'[.\-~]', v) if x.isdigit()]

    pa, pb = parse(a), parse(b)
    if pa > pb:
        return 1
    elif pa < pb:
        return -1
    return 0


def check_git_installed():
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("git is not installed.\n  Install: https://git-scm.com/downloads", file=sys.stderr)
        sys.exit(1)


def run_cmd_in(cwd: Path, cmd: list):
    result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    if result.returncode != 0:
        cmd_str = " ".join(cmd)
        print(f"ERROR: {cmd_str} failed:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
