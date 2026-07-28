# CardputerZero AppBuilder

Build system & developer toolkit for [M5CardputerZero](https://docs.m5stack.com/)
applications. Submit any public Git repository and get a ready-to-install
`.deb` package — no local toolchain required — then publish it to the
CardputerZero AppStore with the Python `czdev` CLI.

- **`czdev`** — a small, pure-**Python 3** CLI to scaffold a new app, authenticate
  with GitHub and publish / unpublish `.deb` packages. No Rust / cargo toolchain
  needed.
- **CI online build** — a GitHub Actions workflow that cross-compiles any repo
  to an aarch64 `.deb`.
- **Examples** — a gallery of ready-to-build apps (C/LVGL, SDL2, Qt, Python,
  Rust) under [`examples/`](examples/).

## Quickstart

[中文](docs/QUICKSTART_ZH.md) | [日本語](docs/QUICKSTART_JA.md)

```bash
git clone https://github.com/CardputerZero/CardputerZero-AppBuilder.git
cd CardputerZero-AppBuilder

./czdev --help                 # works immediately with Python 3
./czdev login                  # one-time GitHub device-flow login
./czdev new my-app             # scaffold a project from the latest template

# From your app's project directory (must contain app-builder.json with a
# "store" section), after producing a .deb:
./czdev bump    --deb build/my_app_1.0.0_arm64.deb   # show next version
./czdev publish --deb build/my_app_1.0.1_arm64.deb   # open a publish PR
```

Requirements: **Python 3**, **git**, **dpkg-deb**.

## The `czdev` CLI

`czdev` is the repo-root wrapper (`./czdev`) around the Python package in
[`scripts/czdev/`](scripts/czdev/). You can also run it as a module:

```bash
PYTHONPATH=scripts python3 -m czdev --help
```

| Command | What it does |
|---|---|
| `czdev new NAME` | Scaffold a new app from the [project template](https://github.com/CardputerZero/Template). See [Starting a new app](#starting-a-new-app). |
| `czdev login` | GitHub OAuth **device flow**; stores a token at `~/.czdev/credentials`. |
| `czdev logout` | Remove the stored GitHub credentials. |
| `czdev bump [--deb PATH]` | Print the next patch version for a package (reads the version from the `.deb`). Defaults to `./build/*.deb`. |
| `czdev publish [--deb PATH]` | Validate the `.deb` and open a publish PR against the `packages` repo. Defaults to `./build/*.deb`. |
| `czdev unpublish NAME --version V [--arch arm64]` | Open a PR that removes a published package version. |

### Ownership model

Package names are **first-come, first-served** by GitHub login: whoever first
publishes a package name owns it. Afterwards only that uploader (or a repo
admin) can publish new versions or unpublish it. The uploader's login is
recorded in the release manifest and enforced server-side — there is no
email-address matching.

### `czdev publish` — end-to-end flow

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                    czdev publish — End-to-End Flow                             │
└──────────────────────────────────────────────────────────────────────────────┘

 ┌─────────┐         ┌─────────┐         ┌──────────┐         ┌─────────────┐
 │  LOGIN  │────────▶│  BUILD  │────────▶│  PUBLISH │────────▶│   REVIEW    │
 └─────────┘         └─────────┘         └──────────┘         └─────────────┘
      │                    │                    │                      │
      ▼                    ▼                    ▼                      ▼
 czdev login          CI workflow          czdev publish           Admin merges
 (GitHub OAuth        ─▶ .deb artifact     --deb xxx.deb           the PR
  Device Flow)                            ┌────────────┐          │
      │                                   │ Preflight: │          ▼
      ▼                                   │ • .desktop │    ┌───────────┐
 Token saved                              │ • version ✓│    │  RELEASE  │
 ~/.czdev/                                │ • size ✓   │    └───────────┘
 credentials                              │ • no root  │          │
      │                                   └─────┬──────┘          ▼
      ▼                                         │           APT repo updated
 Verified emails                                ▼           App live in Store
 (user:email)                             Upload .deb to
                                          GitHub Release
                                          ─▶ metadata PR
                                             on packages
```

```
 Timeline:

 You (Developer)                  czdev                     GitHub (Remote)
 ───────────────                  ─────                     ───────────────
      │                             │                             │
      │── czdev login ─────────────▶│── OAuth Device Flow ──────▶│
      │                             │◀── access token ───────────│
      │                             │                             │
      │── czdev publish ───────────▶│                             │
      │                             │── validate .deb ───────────│ (version/size/root)
      │                             │── upload .deb to Release ──▶│
      │                             │── commit metadata + PR ───▶│
      │◀── PR URL ──────────────────│                             │
      │                             │                             │
      │                             │              Admin reviews & merges
      │                             │              CI rebuilds the APT index
      │                             │                             │
      │◀───────────────────── App available in AppStore ─────────│
      │                                                           │
```

The `.deb` binary is uploaded to a GitHub Release; only small metadata
(`meta.json`, screenshots, icon, release manifest) is committed in the publish
PR. See [`docs/APP_BUILDER_JSON.md`](docs/APP_BUILDER_JSON.md) for the
`store` section that supplies the AppStore listing (title, summary,
screenshots, categories, …).

## Starting a new app

New projects start from [**CardputerZero/Template**](https://github.com/CardputerZero/Template)
(LVGL + CMake, desktop SDL preview + device framebuffer build). Two equivalent
ways to get a copy — both always give you the template's **latest** state:

```bash
# A. via czdev (clones the template's default branch, renames placeholders)
./czdev new my-app

# B. via GitHub's template mechanism (creates a fresh repo under your account)
gh repo create my-app --template CardputerZero/Template --public --clone
```

`czdev new` additionally does the per-app renaming that the template needs:
the CMake project name, the compiled-in `APP_NAME`, the launcher display name,
and — importantly — the icon files. The template's icons install into the
**shared** `/usr/share/APPLaunch/share/images/`, so leaving them named
`template*.png` makes two template-derived packages conflict on install.

```bash
./czdev new my-app --display-name "My App"   # launcher name (default: "My App" from the slug)
./czdev new my-app --dir ~/projects/my-app   # target directory (default: ./my-app)
./czdev new my-app --template me/MyTemplate  # a different template repo, or a git URL
./czdev new my-app --ref dev                 # a different template branch
./czdev new my-app --no-git                  # don't create a git repo
```

`NAME` must be a valid Debian package name (lowercase, starts with a letter),
because it becomes the package name at publish time.

> **Why not a git submodule?** A submodule stores a **gitlink** — an exact
> commit SHA — in the parent tree, so AppBuilder would freeze every developer
> on whichever template commit happened to be vendored, and each template
> update would need a commit here. `czdev new` copies the branch tip at
> scaffold time instead, so there is no pinned commit anywhere. The trade-off
> is that a scaffolded project is *not* linked to upstream: later template
> changes must be cherry-picked manually (which is the normal expectation for
> a scaffold — same as `cargo new`).

## Getting a `.deb`

You don't need a local ARM toolchain — building happens in CI.

### Option A — online build from any repo URL

1. Go to **Actions** → **Build DEB Package** → **Run workflow**.
2. Fill in the form:

   | Field | Required | Example | Description |
   |-------|----------|---------|-------------|
   | **Repository URL** | Yes | `https://github.com/CardputerZero/M5CardputerZero-Launcher.git` | Any public HTTP Git URL (GitHub, GitCode, Gitee, …) |
   | **Branch** | No | `master` | Leave empty to use the repository's default branch |

3. The workflow scans for `app-builder.json` files, builds each project, and
   packages them as `.deb`.
4. Download the `.deb` from the run's **Artifacts** section.

### Option B — build the bundled examples

Pushing to this repo runs **Build APPLaunch .deb packages**
(`.github/workflows/build-debs.yml`), which builds every app under
[`examples/`](examples/) and attaches the `.deb`s as artifacts. Prebuilt
examples are also kept under [`dist/`](dist/).

### Install on device

```bash
scp <package>_arm64.deb pi@<device-ip>:/tmp/
ssh pi@<device-ip> "sudo dpkg -i /tmp/<package>_arm64.deb"
```

## Architecture

The CI pipeline runs on x86_64 and **cross-compiles** to ARM64 (aarch64) using
the `aarch64-linux-gnu-` toolchain — the same approach used by the
[M5Stack_Linux_Libs](https://github.com/m5stack/M5Stack_Linux_Libs) SDK.

```
User Input (repo URL)
        │
        ▼
  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
  │  git clone   │────▶│   discover   │────▶│  build       │────▶│  dpkg-deb    │
  │  --recursive │     │ app-builder  │     │ (x86→arm64)  │     │  packaging   │
  └──────────────┘     │    .json     │     └──────────────┘     └──────────────┘
                       └──────────────┘              │
                              │                       ▼
                        N projects           N × .deb artifacts
                        (parallel)            (download)
```

## DEB Package Structure

Generated packages follow the [APPLaunch packaging conventions](https://github.com/dianjixz/M5CardputerZero-UserDemo/blob/main/doc/APPLaunch-App-%E6%89%93%E5%8C%85%E6%8C%87%E5%8D%97.md):

```
<package>.deb
├── DEBIAN/
│   ├── control
│   ├── postinst      (enable & start systemd service)
│   └── prerm         (stop & disable service)
├── lib/systemd/system/
│   └── <package>.service     (runs as a non-root user; root services are rejected)
└── usr/share/APPLaunch/
    ├── applications/<package>.desktop
    ├── bin/<executable>
    ├── lib/
    └── share/
        ├── font/*.ttf
        └── images/*.png
```

## Troubleshooting

- **`czdev: python3 not found`** — install Python 3 and re-run.
- **`app-builder.json not found`** — run `czdev publish` from your app's
  project directory; the file must contain a `store` section with at least one
  320×170 screenshot.
- **`not the owner of <package>`** — that package name is already owned by
  another GitHub account (first-come, first-served). Pick a different name or
  ask the owner / a repo admin.
- **Publish rejected: service runs as root** — apps must not run as root. Pin
  the bundled systemd service to a non-root user (`User=<non-root>` in the
  `[Service]` section) and rebuild the `.deb`.

## Related Projects

- [M5CardputerZero-UserDemo](https://github.com/dianjixz/M5CardputerZero-UserDemo) — Reference user demo application
- [M5Stack_Linux_Libs](https://github.com/m5stack/M5Stack_Linux_Libs) — SDK with SCons build system
- [m5stack-linux-dtoverlays](https://github.com/m5stack/m5stack-linux-dtoverlays) — Device tree overlays & drivers

## License

MIT
