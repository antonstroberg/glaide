# Glaide

A slide-out drawer of [Claude Code](https://claude.com/claude-code) terminals for Wayland desktops. Press a shortcut and it glides in from the left edge of the screen; click elsewhere and it glides away, while Claude keeps working.

![The drawer sliding in from the left edge, with a Claude tab, a terminal tab, the theme menu and a second Claude tab](docs/demo.webp)

The name is *glide* with an AI in the middle. This is an independent project and is not affiliated with or endorsed by Anthropic.

## What it does

- **One tab per project.** Each tab runs Claude Code in its own folder and continues that folder's last conversation. Plain terminal tabs with your shell sit alongside.
- **Shows what Claude is doing.** A dot on each tab: grey while working, green when finished, orange when it is waiting for your answer, red if it stopped with an error. When the drawer is hidden you get a desktop notification with the first line of the reply or the question; clicking it opens that tab.
- **Screenshot to Claude.** A shortcut lets you select part of the screen and drops the picture into the prompt.
- **Ctrl+click** opens web addresses in the browser and file paths (with or without `:line`) in your editor: VS Code unless you choose another.
- **Pin** it open when you want it beside your work, drag its edge to resize, and give each tab its own colour theme so you can tell projects apart at a glance.

## Requirements

- A Wayland desktop that supports the layer-shell protocol: **KDE Plasma 6**, Sway, Hyprland and other wlroots compositors. It does not work on GNOME or X11.
- Claude Code, installed and signed in.
- Python 3 with PyGObject and pycairo, GTK 4, VTE for GTK 4, gtk4-layer-shell, and `jq`.
- Optional: Spectacle, or `grim` and `slurp`, for screenshots; VS Code or another editor for opening file paths.

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
| Stop the drawer and everything in it | `glaide quit` |

The **+** menu offers three kinds of tab: Claude in a folder you pick (remembered), another Claude session in the current folder (a new conversation, gone when the drawer restarts), and a terminal in the current folder.

## Configuration

Everything lives in `~/.config/glaide/` and is created on first use.

**`projects.conf`** lists the tabs:

```
Website = ~/projects/website
Notes   = ~/notes
Shell   = shell:~/projects/website
```

A new line gets its tab as soon as you save the file, and tabs added with **+** are written here for you. A tab whose line you take out stays open until you close it, so nothing that is running gets cut off.

**`appearance.conf`** holds the look and is re-read the moment you save it:

```
# theme: mocha, black, graphite, midnight, forest, plum, ember or ocean
theme = mocha
# background = #1e1e2e
# text = #cdd6f4
opacity = 1.0
font = Monospace 11
```

**`glaide.conf`** holds the remaining settings. Every line is optional, and a change applies the next time the setting is used:

```
claude = ~/.local/bin/claude
folder = ~/projects
width  = 0.42
editor = zed {file}:{line}
```

- `claude` is the Claude Code program. Glaide looks for `claude` on the `PATH` and in `~/.local/bin`; set this if yours is somewhere else. If it cannot be found, the tab says so and waits for you to press Enter, so you can fix it without restarting.
- `folder` is the folder of the Claude tab you get before listing any projects.
- `width` is how wide the drawer starts, as a share of the screen. Once you drag the edge, the dragged width is used.
- `editor` is the command that opens a file you Ctrl+click, with `{file}`, `{line}` and `{column}` filled in. An editor that runs in a terminal needs one, for example `konsole -e nvim +{line} {file}`.

## How the activity dots work

Glaide starts Claude Code with `--settings` pointing at a small settings file it writes to `~/.cache/glaide/`. That file adds [hooks](https://code.claude.com/docs/en/hooks) which run `hook.sh` when you send a prompt, when Claude finishes, and when it needs an answer. The script tells the drawer over D-Bus which tab it was. Only Claude sessions started by Glaide get these hooks; your own Claude Code settings are not touched, and nothing is sent anywhere but to the drawer on your own machine.

## Good to know

- Claude Code asks you to trust a folder the first time you use it there. It never remembers that answer for your home folder, so give Claude tabs a project folder.
- Two Claude tabs in the same folder share that folder's history: after a restart, the tab continues whichever conversation was used last.
- Interrupting Claude with Esc is not reported by Claude Code, so the drawer clears the "working" dot itself when you press Esc or Ctrl+C.
- A see-through drawer is not blurred; below about 85% opacity, text behind it can get in the way.

## Development

`glaide.py` is the whole program and `hook.sh` the hook. `make test` runs the tests for the parts that read and write text and for the `glaide` command. To try a change without disturbing a running drawer, start a second one under another name:

```sh
export GLAIDE_APP_ID=io.github.example.GlaideTest
GLAIDE_DEBUG=1 GLAIDE_CMD="sh -c 'echo test; sleep 900'" python3 glaide.py
```

`GLAIDE_DEBUG=1` makes it print what it is doing. With `GLAIDE_APP_ID` set, `bin/glaide` talks to that second drawer, so `bin/glaide quit` stops it. `GLAIDE_PROJECTS`, `GLAIDE_APPEARANCE`, `GLAIDE_STATE` and `GLAIDE_SETTINGS` point it at other files than the ones in `~/.config/glaide/`.

## Licence

MIT. See [LICENSE](LICENSE).
