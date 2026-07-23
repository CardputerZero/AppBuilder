# `app-builder.json` schema

Every directory in an application repository that should be discovered, built
and packaged contains one `app-builder.json` at its root. The file plays two
roles:

1. **Packaging** — feeds the CI `.deb` pipeline.
2. **AppStore listing** — the optional `store` section supplies the metadata
   that `czdev publish` (and the web portal at
   [dev.cardputer.cc](https://dev.cardputer.cc)) put on the store page: title,
   summary, screenshots, categories, icon, …

> The older desktop-emulator fields (`runtime`, `entry`, `event_entry`,
> `lvgl_version`, `caps`, `assets`) are still accepted for back-compat but are
> no longer consumed — the Rust `czdev` emulator / `czdev run` loop has been
> removed. New apps only need the packaging fields plus a `store` section.

## Full schema

```jsonc
{
  // ── Packaging (existing) ─────────────────────────────────────────
  "package_name": "hello_cz",       // Debian package name (lowercase, dash)
  "version":      "0.1",            // SemVer-ish; goes into control file
  "app_name":     "Hello CZ",       // Display name in APPLaunch
  "bin_name":     "hello_cz",       // executable / shared-object basename
  "description":  "Hello app",

  // ── AppStore listing (used by `czdev publish` + web portal) ──────
  "store": {
    "summary":     "One-line summary",       // shown in lists
    "description": "Longer detail-page text",// optional; falls back to summary
    "categories":  ["Games"],                // optional; up to a few tags
    "screenshots": ["screenshots/main.png"], // >=1 required; 320×170 PNG(s)
    "icon":        "packaging/icon.png",      // optional; square PNG
    "license":     "MIT",                     // optional
    "source_repo": "https://github.com/you/my_app", // optional
    "author":      { "github": "you" },       // optional; defaults to uploader
    "permissions": [],                        // optional; declared permissions
    "locales":     {}                         // optional; localized title/summary
  },

  // ── Legacy desktop-emulator fields (optional, no longer consumed) ─
  "runtime":       "lvgl-dlopen",   // back-compat only
  "entry":         "app_main",
  "event_entry":   "app_event",
  "lvgl_version":  "9.5",
  "caps":          [],
  "assets":        []
}
```

## Packaging fields

| Field | Required | Type | Default | Notes |
|---|---|---|---|---|
| `package_name` | yes | string | — | Debian package name (lowercase, dash) |
| `version` | no | string | `"0.1"` | goes into the control file |
| `app_name` | no | string | same as `package_name` | display name; also the store title |
| `bin_name` | yes | string | — | executable / shared-object basename |
| `description` | no | string | `""` | control-file description |

## Store listing — `store`

Read by `czdev publish` and the web portal to build the AppStore page. At
least one 320×170 screenshot is required to publish.

| Field | Required | Type | Notes |
|---|---|---|---|
| `store.summary` | recommended | string | one-line summary shown in lists |
| `store.description` | no | string | detail-page text; falls back to `summary` |
| `store.categories` | no | string[] | category tags |
| `store.screenshots` | **yes** | string[] | ≥1 path, relative to the app dir; **320×170** PNG(s) |
| `store.icon` | no | string | square PNG path (else the deb's icon is used) |
| `store.license` | no | string | SPDX id, e.g. `MIT` |
| `store.source_repo` | no | string | public source URL |
| `store.author` | no | object | e.g. `{ "github": "you" }`; defaults to the uploader |
| `store.permissions` | no | string[] | declared permissions (metadata) |
| `store.locales` | no | object | localized `title` / `summary` per locale |

The store title comes from the top-level `app_name`.

## Legacy desktop-emulator fields

`runtime`, `entry`, `event_entry`, `lvgl_version`, `caps`, `assets` were used by
the old Rust `czdev` emulator (`czdev run` / `watch`). That loop has been
removed, so these fields are **no longer consumed** — they are still accepted
(and passed through by CI discovery) for back-compat, but you can safely omit
them from new manifests.
