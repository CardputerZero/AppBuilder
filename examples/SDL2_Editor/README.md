# SDL2_Editor - nano-style text editor

A real, working text editor for the CardputerZero (320x170), in the spirit of
GNU nano. Standalone C + SDL2 + SDL2_ttf, no external services.

## Features

- Line-array text buffer with **insert / delete / newline** editing
- **Vertical and horizontal scrolling** with a line-number gutter
- Blinking text **cursor**; `L:C` position in the status bar
- **Selection** (Shift + arrows / Home / End) with on-screen highlight
- **Cut / copy / paste** through the real system clipboard (SDL clipboard)
- **File open / save / save-as**, with a modified (`*`) indicator and a
  save-on-quit prompt
- nano-style status bar and `Ctrl`-key command set

## Keys

| Key | Action |
|-----|--------|
| arrows / PgUp / PgDn | move cursor / scroll |
| Home / End (or `^A` / `^E`) | start / end of line |
| Shift + move | extend selection |
| `^S` | save (prompts for a name if the buffer is unnamed) |
| `^O` | save-as (edit the name) |
| `^K` | cut selection (or the whole current line) |
| `^C` | copy selection |
| `^U` / `^V` | paste |
| `^G` | select all |
| Backspace / Delete | delete |
| `^Q` / `^X` | quit (asks to save if modified) |
| ESC | cancel a prompt / clear selection |

## Usage

```
editor [file]
```

With no argument it opens an empty buffer. The packaged launcher opens a
default scratch file (`$HOME/notes.txt`) so `^S` always has somewhere to
write; pass a path (or set `EDITOR_FILE`) to edit a specific file.

## Build

`make` (needs `libsdl2-dev`, `libsdl2-ttf-dev`; CI installs them via
`packaging/ci-deps.sh`). Uses the monospace DejaVu font shipped in
`fonts-dejavu-core`.
