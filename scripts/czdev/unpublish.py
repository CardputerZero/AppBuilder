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

    # Packages are referenced by a manifest in git; the .deb itself lives in a
    # Release. GitHub sanitizes '~' to '.' in release asset names.
    asset_name = f"{package}_{version}_{arch}.deb".replace("~", ".")
    file_path = f"pool/main/{package}/{asset_name}.release.json"

    print(f"Checking ownership of {package} {version}...")

    perm = gh.check_permission(TARGET_OWNER, TARGET_REPO)
    is_maintainer = perm >= Permission.WRITE

    # Ownership is first-come-first-served by package name: the recorded uploader
    # (GitHub login) owns it. Only that login or a repo maintainer can remove it.
    # We no longer match the deb's Maintainer email against the caller's emails.
    try:
        manifest_raw = gh.get_file_content(TARGET_OWNER, TARGET_REPO, file_path)
    except FileNotFoundError:
        print("ERROR: package manifest not found in repository", file=sys.stderr)
        sys.exit(1)
    try:
        manifest = json.loads(manifest_raw)
    except json.JSONDecodeError:
        print("ERROR: invalid manifest", file=sys.stderr)
        sys.exit(1)

    owner_login = str(manifest.get("uploaded_by") or "").strip()

    # Legacy manifests (created before ownership was recorded) carry no
    # `uploaded_by`. Fall back to deriving the owner login from the published
    # binary's Maintainer noreply address.
    if not owner_login and manifest.get("url"):
        owner_login = derive_owner_login_from_deb(manifest["url"], asset_name)

    if owner_login:
        if owner_login.lower() != user.login.lower() and not is_maintainer:
            print(f"Cannot unpublish: '{package}' is owned by @{owner_login}.", file=sys.stderr)
            print("  Only the original uploader or a repo maintainer can remove it.", file=sys.stderr)
            sys.exit(1)
        print(f"  ✓ Ownership verified (owner: @{owner_login})")
    elif is_maintainer:
        print("  ✓ No recorded owner; proceeding as repo maintainer")
    else:
        print(f"Cannot unpublish: the owner of '{package}' could not be determined.", file=sys.stderr)
        print("  Ask a repo maintainer to remove it.", file=sys.stderr)
        sys.exit(1)

    # Determine push target
    if is_maintainer:
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
        f"Requested by @{user.login} (owner: @{owner_login or user.login}).\n\n"
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


def login_from_noreply(email: str) -> str:
    """Extract a GitHub login from a noreply email, else "".

    Handles both `login@users.noreply.github.com` and the newer
    `12345+login@users.noreply.github.com` form.
    """
    email = (email or "").strip().lower()
    suffix = "@users.noreply.github.com"
    if not email.endswith(suffix):
        return ""
    local = email[: -len(suffix)]
    if "+" in local:
        local = local.split("+", 1)[1]
    return local


def derive_owner_login_from_deb(deb_url: str, asset_name: str) -> str:
    """Download the published binary and derive the owner login from its
    Maintainer noreply address. Best-effort: returns "" on any failure."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="czdev-unpublish-"))
    try:
        deb_local = tmp_dir / asset_name
        try:
            req = urllib.request.Request(deb_url, headers={"User-Agent": "czdev/0.1"})
            with urllib.request.urlopen(req, timeout=600) as resp, open(deb_local, "wb") as out:
                shutil.copyfileobj(resp, out)
            result = subprocess.run(
                ["dpkg-deb", "-f", str(deb_local), "Maintainer"],
                capture_output=True, text=True, check=True,
            )
        except (Exception, subprocess.CalledProcessError):
            return ""
        return login_from_noreply(extract_email(result.stdout.strip()))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
