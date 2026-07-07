#!/usr/bin/env bash
set -euo pipefail

install -D -m 0755 matrix "$STAGE$APP_INSTALL_DIR/matrix"

# Wayland/KMSDRM launcher wrapper: CM0 runs a labwc Wayland compositor that
# owns /dev/dri/card0, so probe for a live Wayland socket first, then fall
# back kmsdrm -> offscreen. See SDL2_HelloWorld for the rationale.
cat >"$STAGE$INSTALL_PREFIX/bin/$PKG_NAME" <<EOF
#!/bin/sh
LOG=/tmp/$PKG_NAME.log
: >"\$LOG" 2>/dev/null || LOG=/dev/null

if [ -z "\${XDG_RUNTIME_DIR:-}" ]; then
    _uid=\$(id -u 2>/dev/null || echo 1000)
    if [ -d "/run/user/\$_uid" ]; then
        XDG_RUNTIME_DIR="/run/user/\$_uid"
    elif [ -d "/run/user/1000" ]; then
        XDG_RUNTIME_DIR="/run/user/1000"
    fi
    [ -n "\$XDG_RUNTIME_DIR" ] && export XDG_RUNTIME_DIR
fi

_wl_ok=0
if [ -n "\${WAYLAND_DISPLAY:-}" ] && [ -n "\${XDG_RUNTIME_DIR:-}" ] && \\
   [ -S "\$XDG_RUNTIME_DIR/\$WAYLAND_DISPLAY" ]; then
    _wl_ok=1
elif [ -n "\${XDG_RUNTIME_DIR:-}" ]; then
    for _c in wayland-0 wayland-1; do
        if [ -S "\$XDG_RUNTIME_DIR/\$_c" ]; then
            WAYLAND_DISPLAY=\$_c
            export WAYLAND_DISPLAY
            _wl_ok=1
            break
        fi
    done
fi

if [ -z "\${SDL_VIDEODRIVER:-}" ]; then
    if [ "\$_wl_ok" = 1 ]; then
        SDL_VIDEODRIVER=wayland
    elif [ -e /dev/dri/card0 ]; then
        SDL_VIDEODRIVER=kmsdrm
    else
        SDL_VIDEODRIVER=offscreen
    fi
    export SDL_VIDEODRIVER
fi

echo "[$PKG_NAME] driver=\$SDL_VIDEODRIVER WAYLAND_DISPLAY=\${WAYLAND_DISPLAY:-} XDG_RUNTIME_DIR=\${XDG_RUNTIME_DIR:-} uid=\$(id -u)" >>"\$LOG" 2>&1
exec $APP_INSTALL_DIR/matrix "\$@" >>"\$LOG" 2>&1
EOF
chmod 0755 "$STAGE$INSTALL_PREFIX/bin/$PKG_NAME"
