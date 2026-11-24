# Glaide

A slide-out drawer of [Claude Code](https://claude.com/claude-code) terminals for Wayland desktops. Press a shortcut and it glides in from the left edge of the screen; click elsewhere and it glides away, while Claude keeps working.

The name is *glide* with an AI in the middle. This is an independent project and is not affiliated with or endorsed by Anthropic.

## What it does

- **One tab per project.** Each tab runs Claude Code in its own folder and continues that folder's last conversation. Plain terminal tabs with your shell sit alongside.
- **Shows what Claude is doing.** A dot on each tab: grey while working, green when finished, orange when it is waiting for your answer, red if it stopped with an error. When the drawer is hidden you get a desktop notification with the first line of the reply or the question; clicking it opens that tab.
- **Screenshot to Claude.** A shortcut lets you select part of the screen and drops the picture into the prompt.
- **Ctrl+click** opens web addresses in the browser and file paths (with or without `:line`) in VS Code.
- **Pin** it open when you want it beside your work, drag its edge to resize, and give each tab its own colour theme so you can tell projects apart at a glance.

## Requirements

- A Wayland desktop that supports the layer-shell protocol: **KDE Plasma 6**, Sway, Hyprland and other wlroots compositors. It does not work on GNOME or X11.
- Claude Code, installed and signed in.
- Python 3 with PyGObject and pycairo, GTK 4, VTE for GTK 4, gtk4-layer-shell, and `jq`.
- Optional: Spectacle, or `grim` and `slurp`, for screenshots; VS Code for opening file paths.

On Arch Linux and its relatives:

```sh
sudo pacman -S python-gobject python-cairo gtk4 gtk4-layer-shell vte4 jq
```

Glaide is developed and used on KDE Plasma 6. The `grim`/`slurp` screenshot path for other desktops is written but has not been tried yet; reports are welcome.

## Install

```sh
git clone https://github.com/antonstroberg/glaide.git
cd glaide
make install        # into ~/.local; nothing needs root
```

Then run `glaide` once, or press the shortcut.

- **KDE Plasma:** run `kbuildsycoca6` once. The shortcuts are **Meta+C** to show or hide the drawer and **Meta+Shift+C** for a screenshot; change them under System Settings → Shortcuts.
- **Other desktops:** bind a key to `glaide` and, if you like, another to `glaide screenshot`. In Sway, for example: `bindsym Mod4+c exec glaide`.

`make uninstall` removes it again. An Arch package recipe is in `packaging/aur`.

## Using it

| | |
|---|---|
| Show / hide | the shortcut, or `glaide` |
| Switch tab | click, Alt+1…9, Ctrl+PageUp / PageDown |
| New tab | the **+** after the tabs, or Ctrl+Shift+T |
| Close a tab, or give it its own theme | right-click the tab |
| Keep it open | the pin, top right (or `glaide pin`) |
| Theme, transparency, text size | the half-moon button, top right |
| Resize | drag the right edge; remembered per screen |
| Copy / paste | Ctrl+Shift+C / Ctrl+Shift+V |
| Open a link or file | Ctrl+click |
| Screenshot into the prompt | `glaide screenshot` |

The **+** menu offers three kinds of tab: Claude in a folder you pick (remembered), another Claude session in the current folder (a new conversation, gone when the drawer restarts), and a terminal in the current folder.

## Configuration

Everything lives in `~/.config/glaide/` and is created on first use.

**`projects.conf`** lists the tabs:

```
Website = ~/projects/website
Notes   = ~/notes
Shell   = shell:~/projects/website
```

Restart the drawer after editing it by hand (`pkill -f glaide.py`, then open it again). Tabs added with **+** are written here for you.

**`appearance.conf`** holds the look and is re-read the moment you save it:

```
# theme: mocha, black, graphite, midnight, forest, plum, ember or ocean
theme = mocha
# background = #1e1e2e
# text = #cdd6f4
opacity = 1.0
font = Monospace 11
```

A few environment variables change where things are found: `GLAIDE_CLAUDE` (the Claude Code program, if it is not on the `PATH`), `GLAIDE_CWD` (folder of the default Claude tab) and `GLAIDE_WIDTH` (starting width as a share of the screen, default `0.42`).

## How the activity dots work

Glaide starts Claude Code with `--settings` pointing at a small settings file it writes to `~/.cache/glaide/`. That file adds [hooks](https://code.claude.com/docs/en/hooks) which run `hook.sh` when you send a prompt, when Claude finishes, and when it needs an answer. The script tells the drawer over D-Bus which tab it was. Only Claude sessions started by Glaide get these hooks; your own Claude Code settings are not touched, and nothing is sent anywhere but to the drawer on your own machine.

## Good to know

- Claude Code asks you to trust a folder the first time you use it there. It never remembers that answer for your home folder, so give Claude tabs a project folder.
- Two Claude tabs in the same folder share that folder's history: after a restart, the tab continues whichever conversation was used last.
- Interrupting Claude with Esc is not reported by Claude Code, so the drawer clears the "working" dot itself when you press Esc or Ctrl+C.
- A see-through drawer is not blurred; below about 85% opacity, text behind it can get in the way.

## Development

`glaide.py` is the whole program and `hook.sh` the hook. `make test` runs the tests for the parts that read and write text. To try a change without disturbing a running drawer, start a second one under another name:

```sh
GLAIDE_APP_ID=io.github.example.GlaideTest GLAIDE_CMD="sh -c 'echo test; sleep 900'" python3 glaide.py
```

## Licence

MIT. See [LICENSE](LICENSE).
