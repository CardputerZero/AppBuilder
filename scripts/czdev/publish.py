"""Publish a .deb package to the CardputerZero app store — mirrors the Rust publish module."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import auth
from .github_client import GitHubClient, Permission
from .versions import asset_safe, compare_versions, ref_safe

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
    uploader_email = user.email or noreply

    print("Preflight checks:")

    # 1. Check .desktop file exists
    if not check_desktop(deb_path):
        print("ERROR: deb does not contain a .desktop file. All CardputerZero apps must include one.", file=sys.stderr)
        sys.exit(1)
    print("  ✓ .desktop file found")

    # 1b. Apps must not run as root. Reject any bundled systemd *system* service
    #     that would run as root (no User=, User=root/0, and no DynamicUser=yes).
    root_services = find_root_services(deb_path)
    if root_services:
        print("ERROR: apps must not run as root, but these systemd services run as root:", file=sys.stderr)
        for s in root_services:
            print(f"         - {s}", file=sys.stderr)
        print("       Add `User=<non-root>` (e.g. `User=pi`) to the [Service] section, or", file=sys.stderr)
        print("       rebuild with `pack_deb.py --service-user pi` / `--no-service`.", file=sys.stderr)
        sys.exit(1)
    print("  ✓ no root systemd services")

    # 2. Extract metadata. Ownership is first-come-first-served by package name,
    #    keyed on the uploader's GitHub login (recorded in the release manifest
    #    below and enforced server-side). We no longer require the deb's
    #    Maintainer email to match the uploader's GitHub emails — we just record
    #    who uploaded it.
    meta = extract_metadata(deb_path)
    print(f"  ✓ Maintainer: {meta['maintainer'] or meta['maintainer_email'] or '(none)'}")

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

    # 6. Build the meta.json that will be committed and pre-check it against
    #    the store's own policy script BEFORE uploading anything, so a
    #    submission the CI would reject fails right here with the same wording.
    meta_out = build_merged_meta(store_meta, meta, user)
    precheck_store_meta(meta_out, app_dir, store_meta, meta["package"],
                        deb_maintainer=meta.get("maintainer", ""))

    push_owner, push_repo, pr_head_owner = push_target(gh, user)

    # Integrity + canonical asset name. GitHub sanitizes '~' and ':' to '.' in
    # release asset names, so the on-release/manifest filename may differ from
    # the Debian version string (which keeps them).
    file_bytes = Path(deb_path).read_bytes()
    sha256_hash = hashlib.sha256(file_bytes).hexdigest()
    file_size = len(file_bytes)
    deb_name = f"{meta['package']}_{meta['version']}_{meta['architecture']}.deb"
    asset_name = asset_safe(deb_name)
    manifest_name = f"{asset_name}.release.json"
    manifest_path_in_repo = f"pool/main/{meta['package']}/{manifest_name}"
    branch = branch_name(meta)

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
        # Ownership record: first uploader of a package name owns it. The login
        # is the authoritative owner key; the email is kept for contact/audit.
        "uploaded_by": user.login,
        "uploader_email": uploader_email,
    }

    # 2) Commit only metadata (meta.json, screenshots, icon, manifest) to a PR
    #    branch — no .deb, no LFS.
    push_pool_branch(gh, push_owner, push_repo, branch, meta["package"], app_dir, store_meta, meta_out,
                     extra={manifest_name: json.dumps(manifest, indent=2) + "\n"},
                     message=f"publish: {meta['package']} {meta['version']} ({meta['architecture']})")

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
    open_pr(gh, pr_head_owner, branch, f"publish: {meta['package']} {meta['version']}", pr_body)


def run_meta_only():
    """Update the store listing (meta.json, screenshots, icon) without a new
    .deb. The version and binary stay as published; CI checks ownership and the
    metadata policy exactly as for a release, and merges it automatically."""
    check_git_installed()

    raw, app_dir = load_app_builder(Path.cwd())
    pkg = raw.get("package_name", "")
    if not is_valid_package_name(pkg):
        print(f"ERROR: app-builder.json has no valid package_name (got '{pkg}')", file=sys.stderr)
        sys.exit(1)
    store_meta = store_meta_from(raw)
    print(f"Package: {pkg} (store listing only, no new .deb)")
    print()

    if not fetch_url(f"{RAW_BASE}/pool/main/{pkg}/meta.json"):
        print(f"ERROR: '{pkg}' is not published yet. Publish a .deb first with `czdev publish`.", file=sys.stderr)
        sys.exit(1)

    gh = GitHubClient(auth.load_token())
    user = gh.get_user()

    print("Preflight checks:")
    meta_out = build_merged_meta(store_meta, {"package": pkg, "maintainer": ""}, user)
    precheck_store_meta(meta_out, app_dir, store_meta, pkg)

    push_owner, push_repo, pr_head_owner = push_target(gh, user)
    branch = f"metadata/{pkg}-{int(time.time())}"
    push_pool_branch(gh, push_owner, push_repo, branch, pkg, app_dir, store_meta, meta_out,
                     extra={}, message=f"metadata: {pkg} store listing")
    pr_body = (
        f"## Store listing update: `{pkg}`\n\n"
        f"Only `meta.json`, screenshots and the icon change; the published version and "
        f"binary stay as they are.\n\n"
        f"Submitted via `czdev publish --meta-only`."
    )
    open_pr(gh, pr_head_owner, branch, f"metadata: {pkg} (store listing)", pr_body)


def push_target(gh: GitHubClient, user) -> tuple:
    """Where to push the PR branch: the official repo for maintainers, the
    contributor's fork otherwise. Returns (owner, repo, PR head owner or None)."""
    perm = gh.check_permission(TARGET_OWNER, TARGET_REPO)
    if perm >= Permission.WRITE:
        return TARGET_OWNER, TARGET_REPO, None
    print(f"You don't have write access to {TARGET_OWNER}/{TARGET_REPO}.")
    print("  → Forking to your account...  ", end="", flush=True)
    fork_name = gh.fork_repo(TARGET_OWNER, TARGET_REPO)
    print(f"done ({fork_name})")
    owner, repo = fork_name.split("/")
    return owner, repo, user.login


def push_pool_branch(gh: GitHubClient, push_owner: str, push_repo: str, branch: str, pkg: str,
                     app_dir: str, store_meta: dict, meta_out: dict, extra: dict, message: str):
    """Commit pool/main/<pkg>/ (meta.json, screenshots, icon and `extra` files)
    on a new branch of the push target, based on the official repo's main.

    The branch is created through the API at upstream main's commit rather
    than from the fork's own main: a fork that has not been synced in a while
    would otherwise base the PR on old history and conflict. A fork shares its
    object store with upstream, so that commit is already there.
    """
    print(f"Uploading metadata to {TARGET_OWNER}/{TARGET_REPO}...")
    upstream_sha = gh.get_ref_sha(TARGET_OWNER, TARGET_REPO, "heads/main")
    print(f"  → Creating branch {branch} at upstream main ({upstream_sha[:7]})... ", end="", flush=True)
    # A fork that was created a moment ago may not accept refs yet.
    for attempt in range(6):
        try:
            gh.create_ref(push_owner, push_repo, branch, upstream_sha)
            break
        except urllib.error.HTTPError:
            if attempt == 5:
                raise
            time.sleep(5)
    print("done")

    tmp_dir = Path(tempfile.mkdtemp(prefix="czdev-publish-"))
    try:
        run_cmd_in(tmp_dir, ["git", "init"])
        run_cmd_in(tmp_dir, ["git", "remote", "add", "origin", f"git@github.com:{push_owner}/{push_repo}.git"])

        print("  → git fetch (minimal)... ", end="", flush=True)
        run_cmd_in(tmp_dir, ["git", "fetch", "--depth=1", "--filter=blob:none", "origin", branch])
        run_cmd_in(tmp_dir, ["git", "checkout", "-b", branch, "FETCH_HEAD"])
        print("done")

        dest_dir = tmp_dir / "pool" / "main" / pkg
        dest_dir.mkdir(parents=True, exist_ok=True)

        # Copy screenshots
        if store_meta.get("screenshots"):
            screenshots_dest = dest_dir / "screenshots"
            if screenshots_dest.is_dir():
                shutil.rmtree(screenshots_dest)
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

        # meta.json was built and pre-checked before anything was uploaded.
        (dest_dir / "meta.json").write_text(json.dumps(meta_out, indent=2, ensure_ascii=False) + "\n")
        for name, text in extra.items():
            (dest_dir / name).write_text(text)

        print("  → Creating commit... ", end="", flush=True)
        run_cmd_in(tmp_dir, ["git", "add", "-A", f"pool/main/{pkg}"])
        run_cmd_in(tmp_dir, ["git", "commit", "-m", message])
        print("done")

        print("  → Pushing branch... ", end="", flush=True)
        run_cmd_in(tmp_dir, ["git", "push", "origin", branch])
        print("done")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def open_pr(gh: GitHubClient, pr_head_owner: Optional[str], branch: str, title: str, body: str):
    head = f"{pr_head_owner}:{branch}" if pr_head_owner else branch
    print("  → Creating pull request... ", end="", flush=True)
    pr = gh.create_pull_request(TARGET_OWNER, TARGET_REPO, title, body, head, "main")
    print("done")

    print()
    print("✓ Pull request created:")
    print(f"  {pr.html_url}")
    print()
    print("  The PR will be validated by CI. Updates to your own packages merge automatically;")
    print("  a first release waits for a maintainer.")


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

    raw, app_dir = load_app_builder(*search_dirs)
    return store_meta_from(raw), app_dir


def load_app_builder(*search_dirs: Path) -> tuple:
    """The first app-builder.json found, parsed, with the directory it is in."""
    for d in search_dirs:
        manifest_path = d / "app-builder.json"
        if manifest_path.exists():
            return json.loads(manifest_path.read_text()), str(d)

    print("app-builder.json not found in current directory.", file=sys.stderr)
    print("  Run `czdev publish` from your app's project directory.", file=sys.stderr)
    sys.exit(1)


def store_meta_from(raw: dict) -> dict:
    """The store listing fields of an app-builder.json."""
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
    for key in ("description", "locales", "license", "source_repo",
                "icon", "permissions", "share_code"):
        if store.get(key):
            meta[key] = store[key]
    if isinstance(store.get("author"), dict):
        meta["author"] = store["author"]

    if not meta["screenshots"]:
        print("No screenshots defined in app-builder.json store.screenshots.", file=sys.stderr)
        print("  Add at least one 320x170 screenshot to publish.", file=sys.stderr)
        sys.exit(1)
    return meta


RAW_BASE = f"https://raw.githubusercontent.com/{TARGET_OWNER}/{TARGET_REPO}/main"


def fetch_url(url: str, timeout: int = 15) -> Optional[bytes]:
    try:
        return urllib.request.urlopen(urllib.request.Request(url), timeout=timeout).read()
    except Exception:
        return None


def build_merged_meta(store_meta: dict, deb_meta: dict, user) -> dict:
    """The meta.json to publish: the already-published metadata as the base,
    the app-builder.json store section on top.

    Merging matters: fields the store section does not carry — most notably
    the uuid the registry pinned, or a share_code assigned in an earlier
    backfill — must survive an update instead of being wiped by a rewrite.
    Asset paths are rewritten to the pool layout the files are copied into
    (icon at <pkg-dir>/<basename>, screenshots under <pkg-dir>/screenshots/) —
    the app-builder.json values are source-repo relative and would 404 when
    the store resolves them against pool/main/<pkg>/.
    """
    merged = {}
    published = fetch_url(f"{RAW_BASE}/pool/main/{deb_meta['package']}/meta.json")
    if published:
        try:
            merged = json.loads(published.decode("utf-8"))
        except Exception:
            merged = {}

    update = dict(store_meta)
    if update.get("icon"):
        update["icon"] = Path(update["icon"]).name
    if update.get("screenshots"):
        update["screenshots"] = [f"screenshots/{Path(s).name}" for s in update["screenshots"]]
    author = update.pop("author", None)
    for k, v in update.items():
        if v not in (None, "", [], {}):
            merged[k] = v
    merged_author = dict(merged.get("author") or {})
    if isinstance(author, dict):
        merged_author.update({k: v for k, v in author.items() if v})
    merged_author["github"] = user.login
    if not merged_author.get("display_name"):
        display_name = deb_meta["maintainer"].split("<")[0].strip()
        merged_author["display_name"] = display_name or user.login
    merged["author"] = merged_author
    # Publish timestamps: the registry renders these; without them it falls
    # back to overrides or the registry build time, both wrong for updates.
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    merged.setdefault("published_at", now)
    merged["updated_at"] = now
    return merged


def precheck_store_meta(meta_out: dict, app_dir: str, store_meta: dict, pkg: str,
                        deb_maintainer: str = ""):
    """Run the store's own metadata policy locally before uploading anything.

    Uses the exact script CI runs (fetched from the packages repo), staged
    against a temporary pool/main/<pkg>/ layout, so the verdict and the wording
    match what the PR would get. Uniqueness needs the whole pool and is left
    to CI. If the script cannot be fetched (offline), the pre-check is skipped
    and CI remains the authority.
    """
    script = fetch_url(f"{RAW_BASE}/.github/scripts/store_meta_policy.py")
    if not script:
        print("  ⚠ could not fetch store_meta_policy.py — skipping local pre-check (CI will validate)")
        return

    stage = Path(tempfile.mkdtemp(prefix="czdev-precheck-"))
    try:
        pkg_dir = stage / pkg
        (pkg_dir / "screenshots").mkdir(parents=True)
        (pkg_dir / "meta.json").write_text(json.dumps(meta_out, ensure_ascii=False))
        for shot in store_meta.get("screenshots") or []:
            src = Path(app_dir) / shot
            if src.is_file():
                shutil.copy2(src, pkg_dir / "screenshots" / src.name)
        if store_meta.get("icon"):
            icon_src = Path(app_dir) / store_meta["icon"]
            if icon_src.is_file():
                shutil.copy2(icon_src, pkg_dir / icon_src.name)

        script_path = stage / "store_meta_policy.py"
        script_path.write_bytes(script)
        errors_path = stage / "errors.txt"
        warnings_path = stage / "warnings.txt"
        env = dict(os.environ)
        if deb_maintainer:
            env["DEB_MAINTAINER"] = deb_maintainer
        subprocess.run([sys.executable, str(script_path), pkg, str(pkg_dir), "-",
                        str(errors_path), str(warnings_path)], check=False, env=env)
        if warnings_path.is_file():
            for line in warnings_path.read_text(encoding="utf-8").splitlines():
                print(f"  ⚠ {line}")
        if errors_path.is_file():
            print("ERROR: store metadata does not meet the store policy:", file=sys.stderr)
            for line in errors_path.read_text(encoding="utf-8").splitlines():
                print(f"  ✗ {line}", file=sys.stderr)
            print("\n  Fix the \"store\" section of app-builder.json (and the referenced", file=sys.stderr)
            print("  images), then run `czdev publish` again. Nothing was uploaded.", file=sys.stderr)
            sys.exit(1)
        print("  ✓ store metadata passes the store policy")
    finally:
        shutil.rmtree(stage, ignore_errors=True)


# Directories where a *system* systemd unit runs as root by default (user
# units under .../systemd/user/ run as the logged-in user and are not a concern).
_SYSTEMD_SYSTEM_DIRS = ("lib/systemd/system/", "usr/lib/systemd/system/", "etc/systemd/system/")


def service_runs_as_root(text: str) -> bool:
    """Return True if a systemd .service unit would run as root.

    A unit runs as root unless its [Service] section pins it to a non-root
    identity via `User=<non-root>` or `DynamicUser=yes`. An empty/`root`/`0`
    User= (or none) means root.
    """
    in_service = False
    user = None
    dynamic = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            in_service = line.lower() == "[service]"
            continue
        if not in_service or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip().lower()
        val = val.strip()
        if key == "user":
            user = val
        elif key == "dynamicuser":
            dynamic = val.lower() in ("1", "yes", "true", "on")
    if dynamic:
        return False
    if not user:
        return True
    return user in ("root", "0")


def find_root_services(deb_path: str) -> list:
    """List bundled systemd system service units that would run as root."""
    try:
        tar_bytes = subprocess.run(["dpkg-deb", "--fsys-tarfile", deb_path],
                                   capture_output=True, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    import io
    import tarfile
    offenders = []
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tf:
        for m in tf.getmembers():
            if not m.isfile():
                continue
            name = m.name.lstrip("./")
            if not (name.endswith(".service") and name.startswith(_SYSTEMD_SYSTEM_DIRS)):
                continue
            f = tf.extractfile(m)
            if f is None:
                continue
            if service_runs_as_root(f.read().decode("utf-8", "replace")):
                offenders.append(name)
    return offenders


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
    return f"publish/{meta['package']}-{ref_safe(meta['version'])}-{ts}"


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
        print("    NOTE: first releases are reviewed by a human. After the PR is")
        print("    created, post a short video of the app running on a real device")
        print("    in the PR comments; a maintainer will merge it after watching.")
        print("    Later version updates merge automatically.")




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
