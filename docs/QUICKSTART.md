# Quickstart — publish an app to the CardputerZero AppStore

[中文](QUICKSTART_ZH.md) | [日本語](QUICKSTART_JA.md)

Publish a `.deb` package to the AppStore in a few minutes with the pure-Python
`czdev` CLI. No Rust / cargo toolchain and no local ARM toolchain are needed —
building happens in CI.

## 1. Prerequisites

- **Python 3**
- **git**
- **dpkg-deb** (from `dpkg` / `dpkg-dev`)

```bash
# macOS
brew install dpkg
# Debian / Ubuntu
sudo apt install -y python3 git dpkg-dev
```

## 2. Clone and log in

```bash
git clone https://github.com/CardputerZero/CardputerZero-AppBuilder.git
cd CardputerZero-AppBuilder

./czdev --help      # works immediately with Python 3
./czdev login       # GitHub device flow; token saved at ~/.czdev/credentials
```

`czdev login` prints a code and a URL — open the URL, enter the code, and
authorize. The token is reused by later commands.

## 3. Start a project (optional)

If you don't have an app yet, scaffold one from the
[project template](https://github.com/CardputerZero/Template) (LVGL + CMake):

```bash
./czdev new my-app                 # → ./my-app, copied from the template's latest main
# or let GitHub make the copy:
gh repo create my-app --template CardputerZero/Template --public --clone
```

`czdev new` renames the template's placeholders for you — CMake project name,
compiled-in `APP_NAME`, launcher display name, and the icon files (these install
into the *shared* `/usr/share/APPLaunch/share/images/`, so unique names are
required or two template-derived packages conflict). `NAME` must be a valid
Debian package name, since it becomes the package name at publish time.

## 4. Get a `.deb`

You don't build ARM binaries locally. Two ways to obtain a package:

- **Online build** — GitHub **Actions → Build DEB Package → Run workflow**,
  paste your public repo URL, and download the `.deb` artifact.
- **Bundled examples** — pushing to this repo builds everything under
  `examples/`; prebuilt ones are in `dist/`.

Your project must contain an `app-builder.json` (see
[APP_BUILDER_JSON.md](APP_BUILDER_JSON.md)) for CI to discover and build it.

## 5. Add a `store` section for the listing

`czdev publish` reads the AppStore listing from the `store` section of your
`app-builder.json`. At minimum you need a title and one 320×170 screenshot:

```jsonc
{
  "package_name": "my_app",
  "app_name":     "My App",
  "bin_name":     "my_app",
  "version":      "1.0.1",

  "store": {
    "summary":     "One-line description",
    "description": "Longer description shown on the detail page.",
    "categories":  ["Games"],
    "screenshots": ["screenshots/main.png"],   // 320×170 PNG(s)
    "icon":        "packaging/icon.png"          // optional
  }
}
```

## 6. Bump and publish

Run from your app's project directory (the one with `app-builder.json`):

```bash
# See the next patch version implied by the .deb
./czdev bump    --deb build/my_app_1.0.0_arm64.deb

# Publish (the .deb version must be newer than what's already published)
./czdev publish --deb build/my_app_1.0.1_arm64.deb
```

`publish` runs preflight checks (`.desktop` present, version bump, size, and
**no systemd service running as root**), uploads the `.deb` to a GitHub
Release, and opens a metadata PR against the `packages` repo. An admin reviews
and merges it; CI then rebuilds the APT index and your app goes live.

If `--deb` is omitted, `czdev` searches `./build/*.deb`.

## 7. Unpublish

```bash
./czdev unpublish my_app --version 1.0.1
```

This opens a PR that removes that version.

## Notes

- **Ownership is first-come, first-served by GitHub login.** Whoever first
  publishes a package name owns it; only that account (or a repo admin) can
  publish new versions or unpublish it.
- **Apps must not run as root.** If your `.deb` ships a systemd service, pin it
  to a non-root user (`User=<non-root>` in `[Service]`) or publishing is
  rejected.
- You can also publish from the web at **https://dev.cardputer.cc** — drag a
  `.deb`, fill in the store info, and submit.
