"""Scaffold a new app from the CardputerZero project template.

Deliberately *not* a git submodule: a submodule stores a gitlink (an exact
commit SHA) in the parent tree, which would freeze everyone on whatever
template commit was vendored. Instead we shallow-clone the template's default
branch at scaffold time, so `czdev new` always starts you on the latest
template without AppBuilder tracking a template commit at all.

Equivalent one-liner if you'd rather let GitHub do the copy (the template is
marked as a GitHub template repository):

    gh repo create my-app --template CardputerZero/Template --public --clone
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

DEFAULT_TEMPLATE = "CardputerZero/Template"
DEFAULT_REF = "main"

# The template ships placeholder identifiers that must be renamed per app.
# `template_app` is the CMake project name (⇒ binary name, deb package name,
# /usr/share/<name>/ asset root) and the APP_NAME compiled into the asset
# manager. The icons matter too: they install into the *shared*
# /usr/share/APPLaunch/share/images/, so two apps that both ship
# "template.png" would collide at dpkg level.
PLACEHOLDER = "template_app"
ICON_STEM = "template"
DISPLAY_PLACEHOLDER = "TemplateApp"

# Files whose bytes must not be touched by the text substitution pass.
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ttf", ".otf", ".wav",
                   ".mp3", ".bin", ".ico", ".pdf", ".zip"}

# Debian package names: lowercase alnum plus + - . and at least two chars.
# The scaffolded name becomes the package name, so enforce it up front rather
# than letting `czdev publish` reject it later.
NAME_RE = re.compile(r"^[a-z][a-z0-9+.-]+$")


def run(name: str, dir: Optional[str] = None, template: str = DEFAULT_TEMPLATE,
        ref: str = DEFAULT_REF, display_name: Optional[str] = None,
        no_git: bool = False):
    if not NAME_RE.match(name):
        print(f"invalid app name: {name!r}", file=sys.stderr)
        print("must be a valid Debian package name: start with a lowercase "
              "letter, then lowercase letters/digits/+/-/. (2+ chars)", file=sys.stderr)
        sys.exit(1)

    target = Path(dir) if dir else Path(name)
    if target.exists() and any(target.iterdir()):
        print(f"target directory is not empty: {target}", file=sys.stderr)
        sys.exit(1)

    require_git()
    url = template if "://" in template else f"https://github.com/{template}"
    display = display_name or derive_display_name(name)

    print(f"Creating {name} from {template}@{ref}")
    print(f"  → Cloning template... ", end="", flush=True)
    try:
        subprocess.run(["git", "clone", "--quiet", "--depth", "1",
                        "--branch", ref, url, str(target)], check=True)
    except subprocess.CalledProcessError:
        print("failed")
        print(f"could not clone {url} (branch {ref})", file=sys.stderr)
        sys.exit(1)
    upstream_sha = git_output(target, ["rev-parse", "HEAD"])[:12]
    print(f"done ({upstream_sha})")

    # Drop the template's history: this is a scaffold, not a fork. Without
    # this the new project would carry the template's remote and commits.
    shutil.rmtree(target / ".git", ignore_errors=True)

    print("  → Renaming placeholders... ", end="", flush=True)
    renamed_files = rename_icons(target, name)
    patched = substitute(target, name, display)
    print(f"done ({patched} files patched, {renamed_files} icons renamed)")

    if not no_git:
        print("  → Initializing git repository... ", end="", flush=True)
        init_repo(target, name, template, ref, upstream_sha)
        print("done")

    print()
    print(f"✓ Created {target}/")
    print()
    print("  Next steps:")
    print(f"    cd {target}")
    print("    cmake --preset <your-preset>   # see README.md for presets")
    print("    ./czdev publish --deb build/<pkg>.deb")
    print()
    print("  The template was copied at its latest commit and is not linked to"
          " upstream;")
    print("  re-run `czdev new` for a fresh copy, or cherry-pick template"
          " changes manually.")


def require_git():
    if shutil.which("git") is None:
        print("git not found on PATH", file=sys.stderr)
        sys.exit(1)


def git_output(cwd: Path, args: list) -> str:
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True,
                          text=True, check=True).stdout.strip()


def derive_display_name(name: str) -> str:
    """"my-cool-app" → "My Cool App" for launcher/desktop-entry display."""
    return " ".join(part.capitalize() for part in re.split(r"[-_.+]+", name) if part)


def rename_icons(root: Path, name: str) -> int:
    """Rename assets/images/template*.png → <name>*.png.

    These install into the shared /usr/share/APPLaunch/share/images/, so the
    stem must be unique per app or packages conflict on install.
    """
    images = root / "assets" / "images"
    if not images.is_dir():
        return 0
    count = 0
    for path in sorted(images.glob(f"{ICON_STEM}*.png")):
        new_name = name + path.name[len(ICON_STEM):]
        path.rename(images / new_name)
        count += 1
    return count


def substitute(root: Path, name: str, display: str) -> int:
    """Replace the template's placeholder identifiers throughout the tree."""
    replacements = [
        (PLACEHOLDER, name),
        # Icon references: the install glob and the .desktop Icon= line.
        (f"{ICON_STEM}*.png", f"{name}*.png"),
        (f"{ICON_STEM}.png", f"{name}.png"),
        (DISPLAY_PLACEHOLDER, display),
    ]
    patched = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() in BINARY_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        new_text = text
        for old, new in replacements:
            new_text = new_text.replace(old, new)
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
            patched += 1
    return patched


def init_repo(root: Path, name: str, template: str, ref: str, sha: str):
    try:
        subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(
            ["git", "commit", "--quiet", "-m",
             f"chore: scaffold {name} from {template}@{ref} ({sha})"],
            cwd=root, check=True)
    except subprocess.CalledProcessError:
        # A missing user.name/user.email shouldn't fail the whole scaffold —
        # the files are already in place.
        print("(commit skipped)", end="")
