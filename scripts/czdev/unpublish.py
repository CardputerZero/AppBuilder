"""Unpublish (remove) a package from the CardputerZero app store — mirrors the Rust unpublish module."""

import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from . import auth
from .github_client import GitHubClient, Permission

TARGET_OWNER = "CardputerZero"
TARGET_REPO = "packages"


def run(package: str, version: str, arch: str = "arm64"):
    token = auth.load_token()
    gh = GitHubClient(token)
    user = gh.get_user()

    noreply = f"{user.login}@users.noreply.github.com"
    all_emails = [noreply]
    if user.email:
        all_emails.append(user.email)

    # Packages are referenced by a manifest in git; the .deb itself lives in a
    # Release. GitHub sanitizes '~' to '.' in release asset names.
    asset_name = f"{package}_{version}_{arch}.deb".replace("~", ".")
    file_path = f"pool/main/{package}/{asset_name}.release.json"

    print(f"Checking ownership of {package} {version}...")

    # Read the manifest from the repo, download the .deb it points at, and
    # verify the Maintainer matches this user.
    tmp_dir = Path(tempfile.mkdtemp(prefix="czdev-unpublish-"))
    try:
        try:
            manifest_raw = gh.get_file_content(TARGET_OWNER, TARGET_REPO, file_path)
        except FileNotFoundError:
            print("ERROR: package manifest not found in repository", file=sys.stderr)
            sys.exit(1)
        try:
            manifest = json.loads(manifest_raw)
            deb_url = manifest["url"]
        except (json.JSONDecodeError, KeyError):
            print("ERROR: invalid manifest (missing url)", file=sys.stderr)
            sys.exit(1)

        deb_local = tmp_dir / asset_name
        try:
            req = urllib.request.Request(deb_url, headers={"User-Agent": "czdev/0.1"})
            with urllib.request.urlopen(req, timeout=600) as resp, open(deb_local, "wb") as out:
                shutil.copyfileobj(resp, out)
        except Exception as exc:
            print(f"ERROR: could not download package binary: {exc}", file=sys.stderr)
            sys.exit(1)

        try:
            result = subprocess.run(
                ["dpkg-deb", "-f", str(deb_local), "Maintainer"],
                capture_output=True, text=True, check=True,
            )
            maintainer = result.stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            print("ERROR: dpkg-deb not available", file=sys.stderr)
            sys.exit(1)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    maint_email = extract_email(maintainer)
    if not any(e.lower() == maint_email.lower() for e in all_emails):
        print(f"Cannot unpublish: package maintainer '{maintainer}' does not match your account.", file=sys.stderr)
        print("  You can only remove packages you own.", file=sys.stderr)
        sys.exit(1)
    print(f"  ✓ Ownership verified ({maint_email})")

    # Determine push target
    perm = gh.check_permission(TARGET_OWNER, TARGET_REPO, user.login)
    if perm >= Permission.WRITE:
        push_owner = TARGET_OWNER
        push_repo = TARGET_REPO
        pr_head = None
    else:
        fork_name = gh.fork_repo(TARGET_OWNER, TARGET_REPO)
        parts = fork_name.split("/")
        push_owner = parts[0]
        push_repo = parts[1]
        branch = branch_name(package, version)
        pr_head = f"{user.login}:{branch}"

    print("Creating removal PR...")

    # Get base
    base_sha = gh.get_ref_sha(push_owner, push_repo, "heads/main")
    _, base_tree_sha = gh.get_commit(push_owner, push_repo, base_sha)

    # Create tree with file removed (sha: None deletes the entry)
    tree_sha = gh.create_tree(push_owner, push_repo, base_tree_sha, file_path, None)

    # Commit
    commit_msg = f"unpublish: {package} {version}"
    commit_sha = gh.create_commit(push_owner, push_repo, commit_msg, tree_sha, base_sha)

    # Branch
    branch = branch_name(package, version)
    gh.create_ref(push_owner, push_repo, branch, commit_sha)

    # PR
    head = pr_head if pr_head else branch
    pr_body = (
        f"## Remove package: `{package}` v{version}\n\n"
        f"Requested by @{user.login} (maintainer email: {maint_email}).\n\n"
        f"Manifest: `{file_path}`\n\n"
        f"Submitted via `czdev unpublish`. Removing the manifest drops the package "
        f"from the index on the next build; the apt-pool asset can be pruned separately."
    )
    pr = gh.create_pull_request(
        TARGET_OWNER, TARGET_REPO,
        f"unpublish: {package} {version}",
        pr_body, head, "main",
    )

    print()
    print("✓ Removal PR created:")
    print(f"  {pr.html_url}")


def branch_name(package: str, version: str) -> str:
    ts = int(time.time())
    return f"unpublish/{package}-{version}-{ts}"


def extract_email(maintainer: str) -> str:
    start = maintainer.find("<")
    end = maintainer.find(">")
    if start != -1 and end != -1:
        return maintainer[start + 1:end]
    return maintainer
