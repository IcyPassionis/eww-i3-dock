# eww-i3-dock

macOS-style auto-hiding application dock for i3wm/X11, built with Eww 0.5.

## Status

Unfinished. Current priority: performance + auto-hide behavior.

Working:

- hover reveal via `dock_open_area` trigger window,
- pinned apps from `pinned-apps.json`,
- open i3 windows with real `.desktop` icons,
- click-to-focus, Rofi launcher,
- right-click menu: pin/unpin, close/open, move to workspace 1-10
  (numbered workspaces only — pure-named workspaces without a leading
  number are not listed; `N: name` variants work since i3 maps
  `workspace number N` onto them),
- no screen-space reservation (`_NET_WM_STRUT = 0`).

Known issues:

- Eww daemon CPU still needs tuning,
- transparency depends on Picom and needs verification,
- manual pinning is JSON-only, no UI yet.

## Install

```bash
./install.sh
~/.config/eww/start-dock.sh
```

See `AGENTS.md` for constraints and verification.
