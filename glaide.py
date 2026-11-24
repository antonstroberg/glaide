#!/usr/bin/env python3
"""Glaide: Claude Code terminals in a drawer that slides in from the left screen edge.

Tabs
    One tab per project, listed in ~/.config/glaide/projects.conf as
    "Tab name = folder". A tab starts Claude Code the first time it is opened,
    continuing that folder's most recent conversation if there is one.
    "Tab name = shell:folder" makes a plain terminal with your shell instead; one
    called Terminal is added if the file lists none.
    The "+" after the tabs (or Ctrl+Shift+T) adds a tab: Claude in a folder you
    pick (remembered in projects.conf), another Claude session in the current
    folder, or a terminal there. Right-click a tab to close it.
    Switch with a click, Alt+1..9 or Ctrl+PageUp/PageDown.

Activity
    A dot on a tab shows what Claude is doing there: grey = working, green =
    finished, orange = waiting for an answer, red = stopped with an error.
    The last three also send a desktop notification while the drawer is hidden.
    Claude reports these through hooks (see write_hook_settings and hook.sh).

Window
    The pin button (top right) keeps the drawer open when another window is
    clicked; the shortcut still hides it. Pinning is forgotten on restart.
    The width follows the screen the drawer opens on. Drag the right edge to
    change it; the width is remembered per screen in state.json.

Look
    The half-moon button picks a colour theme, the transparency and the text
    size. Right-click a tab to give that tab a theme of its own; the drawer
    takes the colours of the tab you are on. The drawer-wide choices live in
    ~/.config/glaide/appearance.conf, which can also be edited by hand and is
    re-read as soon as it is saved.

Extras
    Ctrl+click opens a link in the browser and a file path (optionally with
    :line) in VS Code. "glaide screenshot" lets you select a part of the screen
    and puts the picture's path into the current tab's prompt.

The program is a single-instance D-Bus service: the "glaide" command only sends
it an action (toggle, pin, screenshot), so a keyboard shortcut reacts instantly.
"""
import json
import math
import os
import pwd
import re
import shlex
import shutil
import signal
import sys
import time
from ctypes import CDLL

import cairo

# gtk4-layer-shell must be loaded before libwayland/GTK.
CDLL("libgtk4-layer-shell.so")

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
gi.require_version("Vte", "3.91")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango, Vte  # noqa: E402
from gi.repository import Gtk4LayerShell as LayerShell  # noqa: E402

APP_ID = os.environ.get("GLAIDE_APP_ID", "io.github.antonstroberg.Glaide")
WIDTH_FRACTION = float(os.environ.get("GLAIDE_WIDTH", "0.42"))  # of screen width
MIN_WIDTH = 420
HANDLE_WIDTH = 6
ANIM_MS = 180
FONT = os.environ.get("GLAIDE_FONT", "Monospace 11")
# The Claude Code program: $GLAIDE_CLAUDE, else "claude" on the PATH, else where its
# installer puts it (a D-Bus started service often has a short PATH).
CLAUDE = (
    os.environ.get("GLAIDE_CLAUDE")
    or shutil.which("claude")
    or os.path.expanduser("~/.local/bin/claude")
)
# Folder of the Claude tab you get before listing any projects.
WORKDIR = os.environ.get("GLAIDE_CWD", os.path.expanduser("~"))
CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "glaide"
)
CACHE_DIR = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "glaide")
PROJECTS_FILE = os.environ.get("GLAIDE_PROJECTS", os.path.join(CONFIG_DIR, "projects.conf"))
APPEARANCE_FILE = os.environ.get("GLAIDE_APPEARANCE", os.path.join(CONFIG_DIR, "appearance.conf"))
STATE_FILE = os.environ.get("GLAIDE_STATE", os.path.join(CONFIG_DIR, "state.json"))
HERE = os.path.dirname(os.path.abspath(__file__))
HOOK_SCRIPT = os.path.join(HERE, "hook.sh")
HOOK_SETTINGS = os.path.join(CACHE_DIR, "claude-settings.json")  # written at start, see below
TEST_CMD = os.environ.get("GLAIDE_CMD")  # run this instead of Claude (for testing)
SHELL = pwd.getpwuid(os.getuid()).pw_shell or "/bin/sh"
SHOT_DIR = os.path.join(CACHE_DIR, "screenshots")
SHOT_KEEP_DAYS = 7

# What Ctrl+click recognises. The path pattern is loose on purpose: a match only
# opens if the file really exists (see link_target).
URL_RE = r"https?://[\w\-.~:/?#\[\]@!$&'()*+,;=%]*[\w\-~/#@$&=+%]"
_NAME = r"[\w.@+\-]+"
_LINE = r"(?::\d+(?::\d+)?)?"
PATH_RE = (
    r"(?<![\w/.@+\-~])(?:"                              # not in the middle of a word
    rf"(?:~|\.{{1,2}})?(?:/{_NAME})+{_LINE}"          # /abs/path, ~/path, ./path
    rf"|{_NAME}(?:/{_NAME})*/[\w@+\-][\w.@+\-]*\.\w+{_LINE}"   # relative/path.py:12
    rf"|[\w@+\-][\w.@+\-]*\.[A-Za-z]\w{{0,7}}:\d+(?::\d+)?"   # file.py:12
    r")"
)
PCRE2_MULTILINE = 0x00000400

# Tab activity: state -> (glyph, colour, tooltip). "idle" shows nothing.
DOTS = {
    "working": ("•", "#7f849c", "working"),
    "done": ("●", "#a6e3a1", "finished"),
    "attention": ("●", "#fab387", "waiting for your answer"),
    "error": ("●", "#f38ba8", "stopped with an error"),
}
NOTIFY = {"done": "Finished", "attention": "Waiting for your answer", "error": "Stopped with an error"}


def write_hook_settings():
    """Write the Claude Code settings file that makes each tab report its activity.

    It is passed to Claude with --settings, so it only affects Claude sessions
    started by the drawer. Each hook runs hook.sh with the state to report.
    """
    def run(state):
        return {"type": "command", "command": f'"{HOOK_SCRIPT}" {state}', "async": True, "timeout": 5}

    hooks = {
        "UserPromptSubmit": [{"hooks": [run("working")]}],
        "Stop": [{"hooks": [run("done")]}],
        "StopFailure": [{"hooks": [run("error")]}],
        "PreToolUse": [{"matcher": "AskUserQuestion|ExitPlanMode", "hooks": [run("attention")]}],
        "Notification": [
            {"matcher": kind, "hooks": [run("attention")]}
            for kind in ("permission_prompt", "elicitation_dialog", "elicitation_url_dialog")
        ],
    }
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(HOOK_SETTINGS, "w") as f:
            json.dump({"hooks": hooks}, f, indent=1)
        return True
    except OSError as e:
        log(f"could not write {HOOK_SETTINGS}: {e}")
        return False


def screenshot_command(path):
    """The command that lets you select a region and saves it to path, or None."""
    if shutil.which("spectacle"):                      # KDE
        return ["spectacle", "-b", "-n", "-r", "-o", path]
    if shutil.which("grim") and shutil.which("slurp"):  # Sway, Hyprland and other wlroots desktops
        return ["sh", "-c", 'grim -g "$(slurp)" "$1"', "glaide-screenshot", path]
    return None


def load_projects():
    """[(tab name, "claude" or "shell", folder, listed in the file)] from the projects file.

    There is always at least one Claude tab and one shell tab.
    """
    projects = []
    try:
        for line in read_text(PROJECTS_FILE).splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, path = (x.strip() for x in line.split("=", 1))
            kind = "claude"
            if path.startswith("shell:"):
                kind, path = "shell", path[6:].strip() or "~"
            path = os.path.expanduser(path)
            if name and os.path.isdir(path):
                projects.append((name, kind, path, True))
    except OSError:
        pass
    if not any(p[1] == "claude" for p in projects):
        projects.insert(0, ("Claude", "claude", WORKDIR, False))
    if not any(p[1] == "shell" for p in projects):
        projects.append(("Terminal", "shell", os.path.expanduser("~"), False))
    return projects


def _project_line(line):
    """(name, kind, folder) of one projects-file line, or None for comments and blanks."""
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    name, path = (x.strip() for x in line.split("=", 1))
    kind = "claude"
    if path.startswith("shell:"):
        kind, path = "shell", path[6:].strip() or "~"
    return name, kind, os.path.normpath(os.path.expanduser(path))


def remember_project(name, path):
    """Add a Claude tab to the projects file."""
    home = os.path.expanduser("~")
    shown = "~" + path[len(home):] if path == home or path.startswith(home + "/") else path
    try:
        os.makedirs(os.path.dirname(PROJECTS_FILE), exist_ok=True)
        old = read_text(PROJECTS_FILE) if os.path.exists(PROJECTS_FILE) else ""
        with open(PROJECTS_FILE, "w") as f:
            f.write(old + ("" if not old or old.endswith("\n") else "\n") + f"{name} = {shown}\n")
    except OSError as e:
        log(f"could not save {PROJECTS_FILE}: {e}")


def forget_project(name, kind, path):
    """Take a tab's line out of the projects file."""
    try:
        lines = read_text(PROJECTS_FILE).splitlines(keepends=True)
        kept = [l for l in lines if _project_line(l) != (name, kind, os.path.normpath(path))]
        if len(kept) != len(lines):
            with open(PROJECTS_FILE, "w") as f:
                f.writelines(kept)
    except OSError as e:
        log(f"could not update {PROJECTS_FILE}: {e}")


def link_target(text, cwd):
    """What a clicked piece of text points at, or None.

    ("url", address) or ("file", path, line, column); relative paths are taken
    from cwd, and a file only counts if it exists.
    """
    text = (text or "").strip()
    if not text:
        return None
    if re.match(r"https?://", text):
        return ("url", text)
    if text.startswith("file://"):
        try:
            text = GLib.filename_from_uri(text)[0]
        except GLib.Error:
            return None
    for candidate in (text, text.rstrip(".,;:")):     # second: without the sentence's full stop
        path, line, col = re.match(r"^(.*?)(?::(\d+)(?::(\d+))?)?$", candidate).groups()
        path = os.path.expanduser(path)
        if not os.path.isabs(path):
            path = os.path.join(cwd, path)
        path = os.path.normpath(path)
        if os.path.exists(path):
            return ("file", path, line, col)
    return None


def open_target(target):
    """Open a link_target result: browser, VS Code for text files, else the default app."""
    if target[0] == "url":
        Gio.AppInfo.launch_default_for_uri(target[1], None)
        return
    _kind, path, line, col = target
    ctype, _uncertain = Gio.content_type_guess(path, None)
    is_text = os.path.isfile(path) and (line or Gio.content_type_is_a(ctype, "text/plain"))
    code = shutil.which("code") or shutil.which("codium")
    if is_text and code:
        where = path + (f":{line}" if line else "") + (f":{col}" if line and col else "")
        Gio.Subprocess.new([code, "--goto", where], Gio.SubprocessFlags.NONE)
    else:
        Gio.AppInfo.launch_default_for_uri(GLib.filename_to_uri(path, None), None)


def load_state():
    try:
        state = json.loads(read_text(STATE_FILE))
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    for key in ("widths", "tab_themes"):     # tab_themes: "name|folder" -> theme name
        if not isinstance(state.get(key), dict):
            state[key] = {}
    return state


def save_state(state):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=1)
    except OSError as e:
        log(f"could not save {STATE_FILE}: {e}")


def read_text(path):
    with open(path) as f:
        return f.read()


def log(msg):
    print(msg, file=sys.stderr, flush=True)


class Tab:
    def __init__(self, uid, name, kind, cwd, term, label, button, saved, fresh):
        self.uid = uid         # stays the same while tabs around it come and go
        self.name, self.kind, self.cwd = name, kind, cwd
        self.term, self.label, self.button = term, label, button
        self.saved = saved     # listed in the projects file
        self.pid = 0           # the program running in it
        self.spawned = False   # Claude (or the shell) has been started in this tab
        self.first = not fresh # next start is the first one: try to continue
        self.started = 0.0     # when its program was last started (monotonic clock)
        self.waiting = False   # its first start is waiting for the terminal to get its size
        self.sized_once = False
        self.state = "idle"    # idle | working | done | attention | error
        self.note = ""         # what the dot is about: start of Claude's reply or question


def rgba(s):
    c = Gdk.RGBA()
    c.parse(s)
    return c


# ---- appearance ---------------------------------------------------------------
THEMES = {   # background, text
    "mocha": ("#1e1e2e", "#cdd6f4"),
    "black": ("#0a0a0c", "#dcdce0"),
    "graphite": ("#1d2023", "#d3d7db"),
    "midnight": ("#0f1420", "#c8d3f0"),
    "forest": ("#0f1a14", "#cfe3d4"),
    "plum": ("#1c1424", "#e0d3ee"),
    "ember": ("#1f1512", "#ecd9cf"),
    "ocean": ("#0d1a1f", "#cde4ea"),
}
OPACITIES = ("1", "0.95", "0.9", "0.85", "0.8", "0.7")
APPEARANCE_TEMPLATE = """\
# Glaide appearance. Changes apply as soon as you save this file.
#
# theme       mocha, black, graphite, midnight, forest, plum, ember or ocean
# background  your own colour (#rrggbb); wins over the theme
# text        your own text colour (#rrggbb); wins over the theme
# opacity     1.0 is solid; 0.85 lets the desktop show through a little
# font        name and size

theme = mocha
# background = #1e1e2e
# text = #cdd6f4
opacity = 1.0
font = Monospace 11
"""


def mix(a, b, t):
    """The colour a share t of the way from a to b, as #rrggbb."""
    return "#" + "".join(
        f"{round((x + (y - x) * t) * 255):02x}"
        for x, y in ((a.red, b.red), (a.green, b.green), (a.blue, b.blue))
    )


def load_appearance():
    """{"bg": RGBA, "fg": RGBA, "opacity": float, "font": str} from the appearance file.

    Anything missing or unreadable falls back to the mocha theme, solid, default font.
    """
    values = {}
    try:
        for line in read_text(APPEARANCE_FILE).splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = (x.strip() for x in line.split("=", 1))
                values[key.lower()] = value
    except OSError:
        pass
    theme = values.get("theme", "").lower()
    if theme not in THEMES:
        theme = "mocha"
    bg_name, fg_name = THEMES[theme]
    look = {"theme": theme}
    for key, config_key, fallback in (("bg", "background", bg_name), ("fg", "text", fg_name)):
        colour = Gdk.RGBA()
        if values.get(config_key) and colour.parse(values[config_key]):
            look["theme"] = "custom"
        else:
            colour.parse(fallback)
        colour.alpha = 1.0
        look[key] = colour
    try:
        look["opacity"] = max(0.3, min(1.0, float(values.get("opacity", "1").replace(",", "."))))
    except ValueError:
        look["opacity"] = 1.0
    look["font"] = values.get("font") or FONT
    return look


def write_appearance(changes):
    """Set keys in the appearance file, keeping its comments. Picking a theme
    switches off hand-set colours, which would otherwise win over it."""
    try:
        lines = read_text(APPEARANCE_FILE).splitlines()
    except OSError:
        lines = APPEARANCE_TEMPLATE.splitlines()
    left = dict(changes)
    out = []
    for line in lines:
        bare = line.strip()
        key = bare.split("=", 1)[0].strip().lower() if "=" in bare and not bare.startswith("#") else None
        if key in left:
            out.append(f"{key} = {left.pop(key)}")
        elif key in ("background", "text") and "theme" in changes:
            out.append("# " + bare)
        else:
            out.append(line)
    out += [f"{key} = {value}" for key, value in left.items()]
    try:
        os.makedirs(os.path.dirname(APPEARANCE_FILE), exist_ok=True)
        with open(APPEARANCE_FILE, "w") as f:
            f.write("\n".join(out) + "\n")
    except OSError as e:
        log(f"could not save {APPEARANCE_FILE}: {e}")


def css_for(look):
    bg, fg = look["bg"], look["fg"]
    surface, muted, border = mix(bg, fg, 0.12), mix(bg, fg, 0.5), mix(bg, fg, 0.22)
    text = mix(fg, fg, 0)
    back = f"rgba({round(bg.red * 255)}, {round(bg.green * 255)}, {round(bg.blue * 255)}, {look['opacity']:.3f})"
    return (
        f"window.glaide {{ background: {back}; }}"
        # The desktop theme gives terminals a solid background of its own.
        "window.glaide vte-terminal { background: transparent; }"
        f"window.glaide .edge {{ background: {border}; min-width: 1px;"
        f"  margin-left: {HANDLE_WIDTH - 1}px; }}"
        f"window.glaide .handle:hover .edge {{ background: {muted}; }}"
        "window.glaide .tabs { padding: 5px 8px 0 8px; }"
        "window.glaide .tabs button { background: none; border: none; box-shadow: none;"
        f"  color: {muted}; padding: 2px 12px; min-height: 0; border-radius: 6px; font-size: 0.9em; }}"
        f"window.glaide .tabs button:hover {{ color: {text}; }}"
        f"window.glaide .tabs button:checked {{ background: {surface}; color: {text}; }}"
        "window.glaide .tabs button.pin { padding: 2px 7px; }"
        "window.glaide .tabs .plus button { padding: 2px 8px; }"
        f"window.glaide .tabs button.pin:checked {{ background: {surface}; color: #f9e2af; }}"
    )


class Drawer(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.win = None
        self.tabs = []
        self.next_uid = 0
        self.tab_box = None
        self.plus_btn = None
        self.cur = 0
        self.shown = False
        self.pinned = False    # stay open when another window gets focus
        self.pin_btn = None
        self.anim_id = 0
        self.width = 640
        self.monitor_key = None    # connector of the screen the drawer is on
        self.monitor_width = 0
        self.grab = None           # while dragging the edge: width minus pointer x
        self.shot_running = False  # a screenshot selection is in progress
        self.menus_open = 0        # open menus: they take the keyboard, which must not hide the drawer
        self.look = None           # colours, opacity and font (see load_appearance)
        self.css = None
        self.css_look = None       # the look the stylesheet was last built from
        self.look_monitor = None
        self.look_timer = 0
        self.state = load_state()

    # ---- setup -------------------------------------------------------------
    def do_startup(self):
        Gtk.Application.do_startup(self)
        GLib.set_application_name("Glaide")
        actions = (
            ("toggle", None, lambda *_: self.toggle()),
            ("pin", None, lambda *_: self.set_pinned(not self.pinned)),
            # "2:done": tab id and new state, sent by hook.sh from inside a tab
            ("tab-state", "s", lambda _a, p: self.on_tab_state(p.get_string())),
            ("show-tab", "i", lambda _a, p: self.show_tab(p.get_int32())),
            ("tab-menu", None, lambda *_: self.open_tab_menu()),
            ("pick-folder", None, lambda *_: self.pick_folder()),
            ("add-folder", "s", lambda _a, p: self.add_folder(p.get_string())),
            ("new-claude-here", None, lambda *_: self.new_here("claude")),
            ("new-terminal", None, lambda *_: self.new_here("shell")),
            ("close-tab", "i", lambda _a, p: self.close_tab(p.get_int32())),
            ("set-width", "i", lambda _a, p: self.set_width(p.get_int32(), save=True)),
            ("screenshot", None, lambda *_: self.screenshot()),
        )
        for name, ptype, handler in actions:
            act = Gio.SimpleAction.new(name, GLib.VariantType.new(ptype) if ptype else None)
            act.connect("activate", handler)
            self.add_action(act)
        # Appearance menu. The first two carry a state so the menu shows the current choice.
        for name, handler in (("theme", self.set_theme), ("opacity", self.set_opacity)):
            act = Gio.SimpleAction.new_stateful(name, GLib.VariantType.new("s"), GLib.Variant("s", ""))
            act.connect("activate", lambda _a, p, handler=handler: handler(p.get_string()))
            self.add_action(act)
        for name, handler in (
            ("font-larger", lambda *_: self.change_font_size(1)),
            ("font-smaller", lambda *_: self.change_font_size(-1)),
            # "3:plum": tab id and theme; an empty theme means "same as the drawer"
            ("tab-theme", lambda _a, p: self.set_tab_theme(p.get_string())),
        ):
            act = Gio.SimpleAction.new(name, GLib.VariantType.new("s") if name == "tab-theme" else None)
            act.connect("activate", handler)
            self.add_action(act)
        self.hooks_ready = os.path.isfile(HOOK_SCRIPT) and write_hook_settings()
        self.hold()  # keep running while hidden

    def do_activate(self):
        self.toggle()

    def build(self):
        win = Gtk.ApplicationWindow(application=self, title="Claude")
        LayerShell.init_for_window(win)
        LayerShell.set_namespace(win, "glaide")
        LayerShell.set_layer(win, LayerShell.Layer.OVERLAY)
        for edge in (LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM, LayerShell.Edge.LEFT):
            LayerShell.set_anchor(win, edge, True)
        LayerShell.set_keyboard_mode(win, LayerShell.KeyboardMode.ON_DEMAND)

        # Best guess until the window is on a screen; corrected in on_enter_monitor.
        mons = Gdk.Display.get_default().get_monitors()
        if mons.get_n_items():
            self.use_monitor(mons.get_item(0))
        win.set_default_size(self.width, -1)

        if not os.path.exists(APPEARANCE_FILE):
            try:
                os.makedirs(os.path.dirname(APPEARANCE_FILE), exist_ok=True)
                with open(APPEARANCE_FILE, "w") as f:
                    f.write(APPEARANCE_TEMPLATE)
            except OSError as e:
                log(f"could not write {APPEARANCE_FILE}: {e}")
        self.look = load_appearance()
        self.sync_appearance_menu()
        self.css = Gtk.CssProvider()
        self.css.load_from_string(css_for(self.look))
        self.css_look = self.look
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), self.css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        win.add_css_class("glaide")
        # Re-read the file whenever it is saved.
        self.look_monitor = Gio.File.new_for_path(APPEARANCE_FILE).monitor_file(
            Gio.FileMonitorFlags.NONE, None
        )
        self.look_monitor.connect("changed", self.on_appearance_file)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_hexpand(True)
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        bar.add_css_class("tabs")
        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        self.tab_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        bar.append(self.tab_box)
        for name, kind, cwd, saved in load_projects():
            self.add_tab(name, kind, cwd, saved=saved)

        menu = Gio.Menu()
        menu.append("Claude in a folder…", "app.pick-folder")
        menu.append("New Claude session here", "app.new-claude-here")
        menu.append("Terminal here", "app.new-terminal")
        plus = Gtk.MenuButton()
        plus.set_label("+")
        plus.set_menu_model(menu)
        plus.set_focusable(False)
        plus.set_tooltip_text("New tab  (Ctrl+Shift+T)")
        plus.add_css_class("plus")
        plus.get_popover().connect("show", lambda *_: self.menu_opened())
        plus.get_popover().connect("closed", lambda *_: self.menu_closed())
        bar.append(plus)
        self.plus_btn = plus

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        bar.append(spacer)

        looks = Gio.Menu()
        section = Gio.Menu()
        for name in THEMES:
            item = Gio.MenuItem.new(name.capitalize(), None)
            item.set_action_and_target_value("app.theme", GLib.Variant("s", name))
            section.append_item(item)
        looks.append_section("Theme", section)
        section = Gio.Menu()
        for value in OPACITIES:
            item = Gio.MenuItem.new("Solid" if value == "1" else f"{round(float(value) * 100)}%", None)
            item.set_action_and_target_value("app.opacity", GLib.Variant("s", value))
            section.append_item(item)
        looks.append_section("Transparency", section)
        section = Gio.Menu()
        section.append("Larger text", "app.font-larger")
        section.append("Smaller text", "app.font-smaller")
        looks.append_section(None, section)
        look_btn = Gtk.MenuButton()
        moon = Gtk.DrawingArea()
        moon.set_content_width(16)
        moon.set_content_height(16)
        moon.set_draw_func(self.draw_moon)
        look_btn.set_child(moon)
        look_btn.set_menu_model(looks)
        look_btn.set_focusable(False)
        look_btn.set_tooltip_text("Theme, transparency and text size")
        look_btn.add_css_class("plus")
        look_btn.get_popover().connect("show", lambda *_: self.menu_opened())
        look_btn.get_popover().connect("closed", lambda *_: self.menu_closed())
        bar.append(look_btn)

        pin = Gtk.ToggleButton()
        icon = Gtk.DrawingArea()     # drawn here: the icon theme's pin is not a pin
        icon.set_content_width(16)
        icon.set_content_height(16)
        icon.set_draw_func(self.draw_pin)
        pin.set_child(icon)
        pin.add_css_class("pin")
        pin.set_focusable(False)
        pin.connect("toggled", lambda b: self.set_pinned(b.get_active()))
        bar.append(pin)
        self.pin_btn = pin
        self.set_pinned(self.pinned)
        box.append(bar)
        box.append(self.stack)

        # Right edge: a thin line you can drag to resize the drawer.
        handle = Gtk.Box()
        handle.add_css_class("handle")
        handle.set_size_request(HANDLE_WIDTH, -1)
        handle.set_cursor(Gdk.Cursor.new_from_name("col-resize"))
        handle.set_tooltip_text("Drag to resize")
        edge = Gtk.Box()
        edge.add_css_class("edge")
        handle.append(edge)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self.on_resize_begin)
        drag.connect("drag-update", self.on_resize_update)
        drag.connect("drag-end", self.on_resize_end)
        handle.add_controller(drag)

        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        root.append(box)
        root.append(handle)
        win.set_child(root)
        win.connect("notify::is-active", self.on_active_changed)
        win.connect("realize", self.on_realize)
        self.win = win
        self.tabs[0].button.set_active(True)
        self.select(0)

    def make_term(self):
        term = Vte.Terminal()
        term.set_scrollback_lines(10000)
        term.set_margin_start(8)
        term.set_margin_end(8 - HANDLE_WIDTH + 1)
        term.set_margin_top(6)
        term.set_margin_bottom(6)
        term.set_vexpand(True)
        term.connect("child-exited", self.on_child_exited)
        term.set_allow_hyperlink(True)    # links the program marks itself
        for pattern in (URL_RE, PATH_RE):
            tag = term.match_add_regex(Vte.Regex.new_for_match(pattern, -1, PCRE2_MULTILINE), 0)
            term.match_set_cursor_name(tag, "pointer")
        click = Gtk.GestureClick()
        click.set_button(Gdk.BUTTON_PRIMARY)
        click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", self.on_click, term)
        term.add_controller(click)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.on_key)
        term.add_controller(keys)
        return term

    def tab_look(self, tab):
        """The drawer's look, with the tab's own theme colours if it has one."""
        theme = self.state["tab_themes"].get(f"{tab.name}|{tab.cwd}")
        if theme not in THEMES:
            return self.look
        look = dict(self.look)
        look["bg"], look["fg"] = rgba(THEMES[theme][0]), rgba(THEMES[theme][1])
        return look

    def style_tab(self, tab):
        look = self.tab_look(tab)
        tab.term.set_font(Pango.FontDescription.from_string(look["font"]))
        tab.term.set_colors(look["fg"], look["bg"], [])
        # When see-through, the window paints the background and the terminal leaves it alone.
        tab.term.set_clear_background(look["opacity"] >= 1.0)

    def style_window(self):
        """Tab bar and background take the colours of the tab on screen."""
        if not self.tabs or self.css is None:
            return
        look = self.tab_look(self.tabs[self.cur])
        if look is not self.css_look:
            self.css_look = look
            self.css.load_from_string(css_for(look))

    def sync_appearance_menu(self):
        """Tick the current theme and transparency in the menu."""
        self.lookup_action("theme").set_state(GLib.Variant("s", self.look["theme"]))
        self.lookup_action("opacity").set_state(GLib.Variant("s", f"{self.look['opacity']:g}"))

    def set_theme(self, name):
        if name in THEMES:
            write_appearance({"theme": name})
            self.apply_appearance()

    def set_opacity(self, value):
        if value in OPACITIES:
            write_appearance({"opacity": value})
            self.apply_appearance()

    def change_font_size(self, step):
        desc = Pango.FontDescription.from_string(self.look["font"])
        size = max(7, min(24, round(desc.get_size() / Pango.SCALE) + step))
        desc.set_size(size * Pango.SCALE)
        write_appearance({"font": desc.to_string()})
        self.apply_appearance()

    def set_tab_theme(self, message):
        uid, _, name = message.partition(":")
        tab = self.tab_by_uid(int(uid)) if uid.isdigit() else None
        if tab is None or (name and name not in THEMES):
            return
        key = f"{tab.name}|{tab.cwd}"
        if name:
            self.state["tab_themes"][key] = name
        else:
            self.state["tab_themes"].pop(key, None)
        save_state(self.state)
        self.style_tab(tab)
        self.style_window()

    def draw_moon(self, area, cr, w, h):
        """A half-filled circle in the button's text colour."""
        c = area.get_color()
        cr.set_source_rgba(c.red, c.green, c.blue, c.alpha)
        r = min(w, h) / 2 - 2
        cr.set_line_width(1.4)
        cr.arc(w / 2, h / 2, r, 0, 2 * math.pi)
        cr.stroke()
        cr.arc(w / 2, h / 2, r, math.pi / 2, 3 * math.pi / 2)
        cr.fill()

    def on_appearance_file(self, *_args):
        if self.look_timer:
            GLib.source_remove(self.look_timer)
        self.look_timer = GLib.timeout_add(200, self.apply_appearance)   # editors save in several steps

    def apply_appearance(self):
        self.look_timer = 0
        self.look = load_appearance()
        log(f"appearance: theme {self.look['theme']}, opacity {self.look['opacity']}, font {self.look['font']}")
        self.sync_appearance_menu()
        for tab in self.tabs:
            self.style_tab(tab)
        self.css_look = None
        self.style_window()
        return GLib.SOURCE_REMOVE

    @property
    def term(self):
        return self.tabs[self.cur].term

    def select(self, i):
        """Show tab i, starting Claude in it if this is its first use."""
        i %= len(self.tabs)
        self.cur = i
        tab = self.tabs[i]
        self.stack.set_visible_child(tab.term)
        self.style_window()
        if not tab.button.get_active():
            tab.button.set_active(True)
        if not tab.spawned:
            self.spawn_when_sized(tab)
        if self.watching():
            self.mark_seen(i)
        tab.term.grab_focus()

    def tab_by_uid(self, uid):
        return next((t for t in self.tabs if t.uid == uid), None)

    def show_tab(self, uid):
        """Open the drawer on the tab with this id (used by notification clicks)."""
        if self.win is None:
            self.build()
        tab = self.tab_by_uid(uid)
        if tab is None:
            return
        self.select(self.tabs.index(tab))
        if not self.shown:
            self.toggle()
        else:
            self.win.present()

    def spawn_when_sized(self, tab, tries=0):
        """Start the tab's program once its terminal has its real size.

        Started any earlier it sees the default 80x24 and lays out its first
        output (a shell greeting, say) for the wrong width.
        """
        if tries == 0:
            if tab.waiting:
                return GLib.SOURCE_REMOVE      # already on its way
            tab.waiting = True
            tab.started = time.monotonic()
        if tab.spawned or tab not in self.tabs:
            tab.waiting = False
            return GLib.SOURCE_REMOVE
        sized = tab.term.get_mapped() and tab.term.get_width() > 1
        if tries < 40 and not (sized and tab.sized_once):
            tab.sized_once = sized             # start one tick after it first has a size
            GLib.timeout_add(25, self.spawn_when_sized, tab, tries + 1)
            return GLib.SOURCE_REMOVE
        tab.waiting = False                    # after a second, start anyway (drawer hidden)
        self.spawn(tab)
        return GLib.SOURCE_REMOVE

    def spawn(self, tab):
        claude = CLAUDE
        hooks = f' --settings "{HOOK_SETTINGS}"' if self.hooks_ready else ""
        if tab.kind == "shell":
            argv = [SHELL]
        elif TEST_CMD:
            argv = shlex.split(TEST_CMD)
        elif tab.first:
            # Pick up the folder's last conversation; plain start if there is none.
            argv = ["/bin/sh", "-c", f'"{claude}"{hooks} --continue || exec "{claude}"{hooks}']
        else:
            argv = ["/bin/sh", "-c", f'exec "{claude}"{hooks}']
        tab.first = False
        tab.spawned = True
        tab.started = time.monotonic()
        env = dict(os.environ)
        if tab.kind == "claude":
            env["GLAIDE_TAB"] = str(tab.uid)   # read by hook.sh
            env["GLAIDE_BUS"] = APP_ID
        tab.term.spawn_async(
            Vte.PtyFlags.DEFAULT, tab.cwd, argv, [f"{k}={v}" for k, v in env.items()],
            GLib.SpawnFlags.DEFAULT, None, None, -1, None, self.on_spawned, tab,
        )

    def on_spawned(self, _term, pid, error, tab):
        if error is not None:
            log(f"could not start the program in tab {tab.name}: {error.message}")
        tab.pid = pid if pid and pid > 0 else 0

    # ---- adding and closing tabs -------------------------------------------
    def add_tab(self, name, kind, cwd, saved=False, fresh=False):
        """Put a tab at the end of the bar. Its program starts when it is first shown."""
        term = self.make_term()
        label = Gtk.Label()
        btn = Gtk.ToggleButton()
        btn.set_child(label)
        btn.set_focusable(False)          # keep keyboard focus in the terminal
        if self.tabs:
            btn.set_group(self.tabs[0].button)
        tab = Tab(self.next_uid, name, kind, cwd, term, label, btn, saved, fresh)
        self.next_uid += 1
        self.style_tab(tab)
        self.stack.add_named(term, f"tab{tab.uid}")
        btn.connect(
            "toggled",
            lambda b: b.get_active() and tab in self.tabs and self.select(self.tabs.index(tab)),
        )
        right = Gtk.GestureClick()
        right.set_button(Gdk.BUTTON_SECONDARY)
        right.connect("pressed", self.on_tab_menu, tab)
        btn.add_controller(right)
        self.tab_box.append(btn)
        self.tabs.append(tab)
        self.update_labels()
        return tab

    def unique_name(self, base):
        names = {t.name for t in self.tabs}
        if base not in names:
            return base
        n = 2
        while f"{base} {n}" in names:
            n += 1
        return f"{base} {n}"

    def open_tab_menu(self):
        if self.win is None:
            self.build()
        if self.shown:
            self.plus_btn.popup()
        else:
            self.toggle()    # the menu can only open once the drawer is on screen
            GLib.timeout_add(ANIM_MS + 120, lambda: self.plus_btn.popup() and False)

    def pick_folder(self):
        dialog = Gtk.FileDialog()
        dialog.set_title("Folder for the new Claude tab")
        start = os.path.expanduser("~/projects")
        dialog.set_initial_folder(Gio.File.new_for_path(start if os.path.isdir(start) else os.path.expanduser("~")))

        was_shown = self.shown    # the dialog takes the keyboard, so the drawer slides out

        def chosen(dlg, result):
            try:
                folder = dlg.select_folder_finish(result)
            except GLib.Error:
                folder = None                # cancelled
            if folder is not None and folder.get_path():
                self.add_folder(folder.get_path())
            elif was_shown and not self.shown:
                self.toggle()

        # No parent window: a layer-shell surface cannot be a dialog's parent.
        dialog.select_folder(None, None, chosen)

    def add_folder(self, path):
        """A remembered Claude tab for this folder; shows the existing one if there is one."""
        if self.win is None:
            self.build()
        path = os.path.normpath(os.path.expanduser(path))
        if not os.path.isdir(path):
            return
        tab = next((t for t in self.tabs if t.kind == "claude" and t.saved and t.cwd == path), None)
        if tab is None:
            base = (os.path.basename(path) or "Claude").replace("=", "-")
            tab = self.add_tab(self.unique_name(base), "claude", path, saved=True)
            remember_project(tab.name, path)
        self.show_tab(tab.uid)

    def new_here(self, kind):
        """Another Claude session (a new conversation) or a terminal in the current tab's folder."""
        if self.win is None:
            self.build()
        cur = self.tabs[self.cur]
        cwd = cur.cwd
        if kind == "claude":
            if cwd == os.path.expanduser("~"):
                cwd = WORKDIR
            base = cur.name if cur.kind == "claude" else "Claude"
        else:
            base = "Terminal"
        tab = self.add_tab(self.unique_name(base), kind, cwd, fresh=True)
        self.show_tab(tab.uid)

    def on_tab_menu(self, gesture, _n_press, _x, _y, tab):
        """Right-click on a tab."""
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        menu = Gio.Menu()
        item = Gio.MenuItem.new("Close tab and forget it" if tab.saved else "Close tab", None)
        item.set_action_and_target_value("app.close-tab", GLib.Variant("i", tab.uid))
        menu.append_item(item)
        current = self.state["tab_themes"].get(f"{tab.name}|{tab.cwd}", "")
        themes = Gio.Menu()
        for name in ("",) + tuple(THEMES):
            label = name.capitalize() if name else "Same as the drawer"
            item = Gio.MenuItem.new(("✓ " if name == current else "    ") + label, None)
            item.set_action_and_target_value("app.tab-theme", GLib.Variant("s", f"{tab.uid}:{name}"))
            themes.append_item(item)
        menu.append_submenu("Theme for this tab", themes)
        pop = Gtk.PopoverMenu.new_from_model(menu)
        pop.set_parent(tab.button)
        pop.set_has_arrow(False)

        def closed(popover):
            def drop():
                if popover.get_parent() is not None:
                    popover.unparent()
                return GLib.SOURCE_REMOVE
            GLib.idle_add(drop)
            self.menu_closed()

        pop.connect("closed", closed)
        self.menu_opened()
        pop.popup()

    def menu_opened(self):
        self.menus_open += 1
        log(f"menu opened ({self.menus_open} open)")

    def menu_closed(self):
        """A menu went away: back to the terminal, or slide out if you clicked elsewhere."""
        self.menus_open = max(0, self.menus_open - 1)
        log(f"menu closed ({self.menus_open} open)")
        self.term.grab_focus()

        def settle():
            if (self.menus_open == 0 and self.shown and not self.pinned
                    and self.grab is None and not self.win.is_active()):
                self.toggle()
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(250, settle)    # give the keyboard time to come back to the drawer

    def close_tab(self, uid):
        """End the tab's program and remove the tab. The last tab stays."""
        tab = self.tab_by_uid(uid)
        if tab is None or len(self.tabs) < 2:
            return
        i = self.tabs.index(tab)
        self.tabs.remove(tab)        # first, so its program ending does not restart it
        self.withdraw_notification(f"tab{tab.uid}")
        if tab.saved:
            forget_project(tab.name, tab.kind, tab.cwd)
        if tab.pid:
            try:
                os.killpg(tab.pid, signal.SIGHUP)   # the program leads its own process group
            except OSError:
                pass
        if i < self.cur or self.cur >= len(self.tabs):
            self.cur -= 1
        self.tabs[self.cur].button.set_active(True)
        tab.button.set_group(None)
        self.tab_box.remove(tab.button)
        self.stack.remove(tab.term)
        self.update_labels()
        self.select(self.cur)

    # ---- tab activity ------------------------------------------------------
    def watching(self):
        """The drawer is open and has the keyboard: you are looking at the current tab."""
        return self.shown and self.win is not None and self.win.is_active()

    def update_label(self, i):
        tab = self.tabs[i]
        markup = GLib.markup_escape_text(tab.name)
        tip = f"{tab.cwd}  (Alt+{i + 1})"
        if tab.state in DOTS:
            glyph, colour, text = DOTS[tab.state]
            markup += f'  <span foreground="{colour}">{glyph}</span>'
            tip += f"\nClaude: {text}"
            if tab.note:
                tip += f"\n{tab.note}"
        if len(self.tabs) > 1:
            tip += "\nRight-click to close"
        tab.label.set_markup(markup)
        tab.button.set_tooltip_text(tip)

    def update_labels(self):
        for i in range(len(self.tabs)):
            self.update_label(i)

    def on_tab_state(self, message):
        """A tab's Claude reports "<index>:<state>:<short text>" (see hook.sh).

        The text says what it is about: the start of Claude's reply or its question.
        """
        index, _, rest = message.partition(":")
        state, _, text = rest.partition(":")
        log(f"tab-state {index}:{state} (+{len(text)} characters)")   # the text stays out of the log
        tab = self.tab_by_uid(int(index)) if index.isdigit() else None
        if tab is None or state not in DOTS:
            return
        i = self.tabs.index(tab)
        if self.watching() and i == self.cur:
            # You can see it happen: no dot for the outcome, no notification.
            state = "working" if state in ("working", "attention") else "idle"
        changed = state != tab.state
        tab.state = state
        tab.note = text.strip() if state in NOTIFY else ""
        self.update_label(i)
        if state == "working":
            self.withdraw_notification(f"tab{tab.uid}")
        elif changed and state in NOTIFY and not self.shown:
            note = Gio.Notification.new(f"{tab.name}: {NOTIFY[state].lower()}")
            # Plasma reads the body as markup, so these three have to be escaped.
            note.set_body(tab.note.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
            note.set_default_action_and_target("app.show-tab", GLib.Variant("i", tab.uid))
            self.send_notification(f"tab{tab.uid}", note)

    def mark_seen(self, i):
        """You looked at tab i: clear its dot and notification."""
        tab = self.tabs[i]
        if tab.state in ("done", "error"):
            tab.state = "idle"
            tab.note = ""
        elif tab.state == "attention":
            tab.state = "working"   # Claude is mid-task and continues once you answer
            tab.note = ""
        self.update_label(i)
        self.withdraw_notification(f"tab{tab.uid}")

    # ---- width -------------------------------------------------------------
    def use_monitor(self, monitor):
        """Take the width for this screen: the remembered one, else a share of it."""
        self.monitor_key = monitor.get_connector() or "default"
        self.monitor_width = monitor.get_geometry().width
        saved = self.state["widths"].get(self.monitor_key)
        want = saved if isinstance(saved, int) else int(self.monitor_width * WIDTH_FRACTION)
        self.set_width(want)

    def set_width(self, width, save=False):
        limit = self.monitor_width - 80 if self.monitor_width else 100000
        width = max(MIN_WIDTH, min(int(width), limit))
        if save and self.monitor_key:
            self.state["widths"][self.monitor_key] = width
            save_state(self.state)
        if width == self.width:
            return
        self.width = width
        if self.win is not None:
            self.win.set_default_size(width, -1)
            if not self.shown and not self.anim_id:
                LayerShell.set_margin(self.win, LayerShell.Edge.LEFT, -width)

    def on_realize(self, win):
        self.on_monitors = []   # screens the window is on, most recently entered last
        win.get_surface().connect("enter-monitor", self.on_monitor_change, True)
        win.get_surface().connect("leave-monitor", self.on_monitor_change, False)

    def on_monitor_change(self, _surface, monitor, entered):
        if monitor in self.on_monitors:
            self.on_monitors.remove(monitor)
        if entered:
            self.on_monitors.append(monitor)
        if self.on_monitors and self.grab is None:
            current = self.on_monitors[-1]
            log(f"on screen {current.get_connector()} ({current.get_geometry().width} wide)")
            self.use_monitor(current)

    def pointer_x(self, gesture):
        """Pointer position across the window; steady while the window resizes."""
        event = gesture.get_last_event(gesture.get_last_updated_sequence())
        if event is None:
            return None
        ok, x, _y = event.get_position()
        return x if ok else None

    def on_resize_begin(self, gesture, _x, _y):
        x = self.pointer_x(gesture)
        self.grab = None if x is None else self.width - x

    def on_resize_update(self, gesture, _dx, _dy):
        x = self.pointer_x(gesture)
        if self.grab is not None and x is not None:
            self.set_width(x + self.grab)

    def on_resize_end(self, _gesture, _dx, _dy):
        if self.grab is not None:
            self.set_width(self.width, save=True)
        self.grab = None
        self.term.grab_focus()

    # ---- links and screenshots ---------------------------------------------
    def on_click(self, gesture, _n_press, x, y, term):
        """Ctrl+click on a link or file path opens it."""
        if not gesture.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK:
            return
        text = term.check_hyperlink_at(x, y) or term.check_match_at(x, y)[0]
        tab = next((t for t in self.tabs if t.term is term), None)
        target = link_target(text, tab.cwd) if text and tab else None
        if target is None:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)   # not a click for the program
        try:
            open_target(target)
        except GLib.Error as e:
            log(f"could not open link: {e.message}")

    def screenshot(self):
        """Select a part of the screen; its file path goes into the current tab's prompt."""
        if self.shot_running:
            return
        if self.win is None:
            self.build()
        os.makedirs(SHOT_DIR, exist_ok=True)
        cutoff = time.time() - SHOT_KEEP_DAYS * 86400
        for old in os.scandir(SHOT_DIR):
            if old.is_file() and old.stat().st_mtime < cutoff:
                os.unlink(old.path)
        path = os.path.join(SHOT_DIR, time.strftime("screen-%Y%m%d-%H%M%S.png"))
        was_shown = self.shown
        self.shot_running = True
        if self.shown:
            self.toggle()    # get out of the picture

        def done(proc, result):
            self.shot_running = False
            try:
                proc.wait_finish(result)
            except GLib.Error:
                pass
            if os.path.isfile(path) and os.path.getsize(path) > 0:
                tab = self.tabs[self.cur]
                self.show_tab(tab.uid)
                # A program that is still starting up garbles a paste: give it 3 seconds.
                wait = max(0.0, 3.0 - (time.monotonic() - tab.started))
                GLib.timeout_add(int(wait * 1000) + 1, lambda: tab.term.paste_text(path + " ") and False)
            elif was_shown and not self.shown:
                self.toggle()    # cancelled: back to how it was

        def start():
            command = screenshot_command(path)
            try:
                if command is None:
                    raise GLib.Error("neither spectacle nor grim and slurp are installed")
                proc = Gio.Subprocess.new(command, Gio.SubprocessFlags.STDERR_SILENCE)
                proc.wait_async(None, done)
            except GLib.Error as e:
                self.shot_running = False
                log(f"could not start the screenshot tool: {e.message}")
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(ANIM_MS + 80 if was_shown else 1, start)

    # ---- events ------------------------------------------------------------
    def on_child_exited(self, term, _status):
        # Claude quit (/exit, Ctrl+C twice): start a fresh session in that tab,
        # and hide the drawer if it was the tab on screen.
        i = next((n for n, t in enumerate(self.tabs) if t.term is term), None)
        if i is None:
            return
        tab = self.tabs[i]
        tab.state = "idle"
        tab.note = ""
        self.update_label(i)
        self.withdraw_notification(f"tab{tab.uid}")
        term.reset(True, True)
        self.spawn(tab)
        if self.shown and not self.pinned and i == self.cur:
            self.toggle()

    def draw_pin(self, area, cr, w, h):
        """A pushpin in the button's text colour: tilted when free, upright when pinned."""
        c = area.get_color()
        cr.set_source_rgba(c.red, c.green, c.blue, c.alpha)
        cr.translate(w / 2, h / 2)
        cr.scale(min(w, h) / 16, min(w, h) / 16)
        if not self.pinned:
            cr.rotate(math.pi / 4)
        cr.translate(-8, -8)
        cr.rectangle(5, 1.2, 6, 1.8)                    # head
        cr.fill()
        cr.move_to(6.2, 3)                              # body, widening to the collar
        for x, y in ((9.8, 3), (10.4, 7), (12.4, 9.4), (3.6, 9.4), (5.6, 7)):
            cr.line_to(x, y)
        cr.close_path()
        cr.fill()
        cr.set_line_width(1.3)                          # needle
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.move_to(8, 9.4)
        cr.line_to(8, 14.8)
        cr.stroke()

    def set_pinned(self, pinned):
        """Pinned: the drawer stays open when it loses focus."""
        self.pinned = pinned
        if self.pin_btn is None:
            return
        if self.pin_btn.get_active() != pinned:
            self.pin_btn.set_active(pinned)   # re-enters once with the same value
        self.pin_btn.get_child().queue_draw()
        self.pin_btn.set_tooltip_text(
            "Pinned: stays open. Click to let it slide away again."
            if pinned else "Pin: keep the drawer open when you click elsewhere"
        )
        if self.shown:
            self.term.grab_focus()

    def on_active_changed(self, win, _pspec):
        log(f"is-active={win.is_active()} shown={self.shown} pinned={self.pinned}")
        if self.watching():
            self.mark_seen(self.cur)
        # Lost keyboard focus (clicked another window/desktop): slide out, unless pinned.
        if (self.shown and not self.pinned and not win.is_active()
                and self.grab is None and self.menus_open == 0):
            self.toggle()

    def on_key(self, _ctl, keyval, _code, state):
        cs = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK
        # Esc or Ctrl+C can interrupt Claude, and an interrupt is not reported:
        # drop the "working" dot rather than leave it stuck.
        interrupt = keyval == Gdk.KEY_Escape or (
            (state & cs) == Gdk.ModifierType.CONTROL_MASK and keyval in (Gdk.KEY_C, Gdk.KEY_c)
        )
        if interrupt and self.tabs[self.cur].state == "working":
            self.tabs[self.cur].state = "idle"
            self.update_label(self.cur)
        if (state & cs) == cs:
            if keyval in (Gdk.KEY_C, Gdk.KEY_c):
                self.term.copy_clipboard_format(Vte.Format.TEXT)
                return True
            if keyval in (Gdk.KEY_V, Gdk.KEY_v):
                self.term.paste_clipboard()
                return True
            if keyval in (Gdk.KEY_T, Gdk.KEY_t):
                self.open_tab_menu()
                return True
        if state & Gdk.ModifierType.ALT_MASK and Gdk.KEY_1 <= keyval <= Gdk.KEY_9:
            if keyval - Gdk.KEY_1 < len(self.tabs):
                self.select(keyval - Gdk.KEY_1)
            return True
        if state & Gdk.ModifierType.CONTROL_MASK and keyval in (Gdk.KEY_Page_Down, Gdk.KEY_Page_Up):
            self.select(self.cur + (1 if keyval == Gdk.KEY_Page_Down else -1))
            return True
        return False

    # ---- slide animation ---------------------------------------------------
    def toggle(self):
        if self.win is None:
            self.build()
        self.shown = not self.shown
        log(f"toggle -> shown={self.shown}")
        start_margin = LayerShell.get_margin(self.win, LayerShell.Edge.LEFT)
        if self.shown:
            if not self.win.get_visible():
                start_margin = -self.width
                LayerShell.set_margin(self.win, LayerShell.Edge.LEFT, start_margin)
                self.win.set_visible(True)
            self.win.present()
            self.term.grab_focus()
        if self.anim_id:
            self.win.remove_tick_callback(self.anim_id)
        t0 = time.monotonic()

        def tick(_w, _clock):
            # Read the width each frame: it can change when the screen becomes known.
            target = 0 if self.shown else -self.width
            p = min(1.0, (time.monotonic() - t0) * 1000 / ANIM_MS)
            e = 1 - (1 - p) ** 3  # ease-out cubic
            LayerShell.set_margin(
                self.win, LayerShell.Edge.LEFT, round(start_margin + (target - start_margin) * e)
            )
            if p < 1.0:
                return GLib.SOURCE_CONTINUE
            self.anim_id = 0
            if not self.shown:
                self.win.set_visible(False)
            return GLib.SOURCE_REMOVE

        self.anim_id = self.win.add_tick_callback(tick)


if __name__ == "__main__":
    Drawer().run(sys.argv)
