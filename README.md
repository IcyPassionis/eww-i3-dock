# eww-i3-dock

A macOS-style auto-hiding application dock for **i3wm on X11**, built with
**Eww 0.5**. Pinned launchers, open windows (click to focus), a Rofi launcher
button and a right-click menu (pin/unpin, close, move to workspace 1-10). The
dock reserves no screen space (`_NET_WM_STRUT = 0`) and appears when the pointer
touches the bottom edge.

Inspired by the dock look of [eww-mac-shell](https://github.com/I-Want-ToBelieve/eww-mac-shell)
(Hyprland only); all window logic here is i3/X11-specific.

## Status

Unfinished. Priorities: performance and auto-hide first, then appearance.
Known issues: Eww daemon CPU still needs tuning; transparency needs a
compositor such as Picom.

## Requirements

`eww` 0.5 (not 0.6), `i3`, `python3`, `xdotool`, `xprop`, `rofi`, `flock`,
`pactl` or equivalent for the volume popover, and a compositor for transparency.

## Install

```sh
./install.sh                 # copies src/* to ~/.config/eww (or $DOCK_CONFIG_DIR)
~/.config/eww/start-dock.sh  # also call this from your i3 config
```

`install.sh` never overwrites an existing `pinned-apps.json`; pass
`--replace-pins` to reset it. After upgrading from an older install, restart the
dock daemon once so it picks up the new `PATH`.

## Configure

- **Theme:** edit `src/theme.scss` (colours and font), run `./install.sh`, restart the dock.
- **Pins:** use the right-click menu, or edit `~/.config/eww/pinned-apps.json`:
  `[{"name": "Firefox", "desktop": "firefox.desktop", "match": "firefox", "command": "gtk-launch firefox.desktop"}]`.
  Starts empty.

## License

GPL-2.0, see `LICENSE`.
