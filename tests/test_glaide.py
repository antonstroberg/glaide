"""Tests for the parts of Glaide that read and write text.

Run with: make test
They need the same libraries as Glaide itself (GTK 4, VTE, gtk4-layer-shell) but
open no window.
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import glaide  # noqa: E402


class TempFiles(unittest.TestCase):
    """Points Glaide's files at a temporary folder for the duration of a test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.saved = {name: getattr(glaide, name) for name in (
            "PROJECTS_FILE", "APPEARANCE_FILE", "STATE_FILE", "CACHE_DIR", "HOOK_SETTINGS")}
        glaide.PROJECTS_FILE = os.path.join(self.dir, "projects.conf")
        glaide.APPEARANCE_FILE = os.path.join(self.dir, "appearance.conf")
        glaide.STATE_FILE = os.path.join(self.dir, "state.json")
        glaide.CACHE_DIR = os.path.join(self.dir, "cache")
        glaide.HOOK_SETTINGS = os.path.join(glaide.CACHE_DIR, "claude-settings.json")

    def tearDown(self):
        for name, value in self.saved.items():
            setattr(glaide, name, value)
        self.tmp.cleanup()

    def write(self, path, text):
        with open(path, "w") as f:
            f.write(text)


class LinkTarget(TempFiles):
    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(self.dir, "src"))
        self.write(os.path.join(self.dir, "src", "app.py"), "x\n")
        self.write(os.path.join(self.dir, "notes.md"), "y\n")

    def test_web_address(self):
        self.assertEqual(glaide.link_target("https://example.com/a?b=1", self.dir),
                         ("url", "https://example.com/a?b=1"))

    def test_relative_path_with_line_and_column(self):
        path = os.path.join(self.dir, "src", "app.py")
        self.assertEqual(glaide.link_target("src/app.py:12", self.dir), ("file", path, "12", None))
        self.assertEqual(glaide.link_target("src/app.py:12:3", self.dir), ("file", path, "12", "3"))
        self.assertEqual(glaide.link_target("./src/app.py", self.dir), ("file", path, None, None))

    def test_absolute_path_and_file_uri(self):
        path = os.path.join(self.dir, "notes.md")
        self.assertEqual(glaide.link_target(path + ":7", self.dir), ("file", path, "7", None))
        self.assertEqual(glaide.link_target("file://" + path, "/"), ("file", path, None, None))

    def test_full_stop_after_the_path_is_ignored(self):
        path = os.path.join(self.dir, "notes.md")
        self.assertEqual(glaide.link_target("notes.md.", self.dir), ("file", path, None, None))
        self.assertEqual(glaide.link_target("src/app.py:12.", self.dir)[2], "12")

    def test_things_that_are_not_files(self):
        for text in ("and/or", "1/2", "missing/file.py:3", "nope.md.", "", None):
            self.assertIsNone(glaide.link_target(text, self.dir), text)


class Projects(TempFiles):
    def test_lines(self):
        home = os.path.expanduser("~")
        self.assertEqual(glaide._project_line("Docs = ~"), ("Docs", "claude", home))
        self.assertEqual(glaide._project_line("Shell = shell:~"), ("Shell", "shell", home))
        self.assertEqual(glaide._project_line("Shell = shell:"), ("Shell", "shell", home))
        self.assertEqual(glaide._project_line("a=b = /tmp"), ("a", "claude", "b = /tmp"))   # first "=" splits
        for line in ("", "# comment", "no equals sign"):
            self.assertIsNone(glaide._project_line(line))

    def test_always_one_claude_tab_and_one_terminal(self):
        tabs = glaide.load_projects()            # no file at all
        self.assertEqual([t[1] for t in tabs], ["claude", "shell"])
        self.assertEqual([t[3] for t in tabs], [False, False])

    def test_file_tabs_and_missing_folders(self):
        self.write(glaide.PROJECTS_FILE,
                   f"# comment\nOne = {self.dir}\nGone = {self.dir}/nowhere\nSh = shell:{self.dir}\n")
        self.assertEqual(glaide.load_projects(),
                         [("One", "claude", self.dir, True), ("Sh", "shell", self.dir, True)])

    def test_remember_and_forget(self):
        self.write(glaide.PROJECTS_FILE, "# mine\nOne = /tmp")      # no newline at the end
        glaide.remember_project("Two", self.dir)
        self.assertEqual(glaide.read_text(glaide.PROJECTS_FILE), f"# mine\nOne = /tmp\nTwo = {self.dir}\n")
        glaide.forget_project("One", "claude", "/tmp")
        self.assertEqual(glaide.read_text(glaide.PROJECTS_FILE), f"# mine\nTwo = {self.dir}\n")
        glaide.forget_project("Two", "shell", self.dir)             # other kind: not the same tab
        self.assertIn("Two", glaide.read_text(glaide.PROJECTS_FILE))

    def test_home_is_written_as_tilde(self):
        glaide.remember_project("Home", os.path.expanduser("~/somewhere"))
        self.assertIn("Home = ~/somewhere\n", glaide.read_text(glaide.PROJECTS_FILE))


class Appearance(TempFiles):
    def test_defaults_without_a_file(self):
        look = glaide.load_appearance()
        self.assertEqual((look["theme"], look["opacity"], look["font"]), ("mocha", 1.0, glaide.FONT))
        self.assertEqual(glaide.mix(look["bg"], look["bg"], 0), glaide.THEMES["mocha"][0])

    def test_theme_opacity_and_font(self):
        self.write(glaide.APPEARANCE_FILE, "theme = Black\nopacity = 0,85\nfont = Monospace 13\n")
        look = glaide.load_appearance()
        self.assertEqual((look["theme"], look["opacity"], look["font"]), ("black", 0.85, "Monospace 13"))
        self.assertEqual(glaide.mix(look["fg"], look["fg"], 0), glaide.THEMES["black"][1])

    def test_own_colours_win_and_bad_values_fall_back(self):
        self.write(glaide.APPEARANCE_FILE,
                   "theme = nonsense\nbackground = #203040\ntext = notacolour\nopacity = banana\n")
        look = glaide.load_appearance()
        self.assertEqual(look["theme"], "custom")
        self.assertEqual(glaide.mix(look["bg"], look["bg"], 0), "#203040")
        self.assertEqual(glaide.mix(look["fg"], look["fg"], 0), glaide.THEMES["mocha"][1])
        self.assertEqual(look["opacity"], 1.0)

    def test_opacity_limits(self):
        self.write(glaide.APPEARANCE_FILE, "opacity = 0.05\n")
        self.assertEqual(glaide.load_appearance()["opacity"], 0.3)
        self.write(glaide.APPEARANCE_FILE, "opacity = 7\n")
        self.assertEqual(glaide.load_appearance()["opacity"], 1.0)

    def test_menu_choice_keeps_comments_and_switches_off_own_colours(self):
        self.write(glaide.APPEARANCE_FILE,
                   "# my notes\ntheme = mocha\nbackground = #203040\n# text = #ffffff\nopacity = 1.0\n")
        glaide.write_appearance({"theme": "plum"})
        self.assertEqual(glaide.read_text(glaide.APPEARANCE_FILE),
                         "# my notes\ntheme = plum\n# background = #203040\n# text = #ffffff\nopacity = 1.0\n")
        self.assertEqual(glaide.load_appearance()["theme"], "plum")

    def test_menu_choice_adds_a_missing_key_and_makes_the_file(self):
        glaide.write_appearance({"opacity": "0.9"})
        self.assertEqual(glaide.load_appearance()["opacity"], 0.9)
        self.write(glaide.APPEARANCE_FILE, "theme = black\n")
        glaide.write_appearance({"font": "Monospace 12"})
        self.assertEqual(glaide.read_text(glaide.APPEARANCE_FILE), "theme = black\nfont = Monospace 12\n")

    def test_mix(self):
        black, white = glaide.rgba("#000000"), glaide.rgba("#ffffff")
        self.assertEqual(glaide.mix(black, white, 0), "#000000")
        self.assertEqual(glaide.mix(black, white, 1), "#ffffff")
        self.assertEqual(glaide.mix(black, white, 0.5), "#808080")

    def test_stylesheet_carries_the_opacity(self):
        self.write(glaide.APPEARANCE_FILE, "theme = black\nopacity = 0.8\n")
        css = glaide.css_for(glaide.load_appearance())
        self.assertIn("rgba(10, 10, 12, 0.800)", css)
        provider = glaide.Gtk.CssProvider()
        provider.load_from_string(css)          # must parse


class State(TempFiles):
    def test_missing_and_broken_files(self):
        self.assertEqual(glaide.load_state(), {"widths": {}, "tab_themes": {}})
        self.write(glaide.STATE_FILE, "not json")
        self.assertEqual(glaide.load_state(), {"widths": {}, "tab_themes": {}})
        self.write(glaide.STATE_FILE, '{"widths": {"DP-1": 900}, "tab_themes": []}')
        self.assertEqual(glaide.load_state(), {"widths": {"DP-1": 900}, "tab_themes": {}})

    def test_round_trip(self):
        glaide.save_state({"widths": {"eDP-1": 640}, "tab_themes": {"A|/x": "plum"}})
        self.assertEqual(glaide.load_state()["tab_themes"], {"A|/x": "plum"})


class HookSettings(TempFiles):
    def test_every_hook_runs_the_script_in_the_background(self):
        self.assertTrue(glaide.write_hook_settings())
        hooks = json.loads(glaide.read_text(glaide.HOOK_SETTINGS))["hooks"]
        self.assertEqual(set(hooks), {"UserPromptSubmit", "Stop", "StopFailure", "PreToolUse", "Notification"})
        states = set()
        for groups in hooks.values():
            for group in groups:
                for hook in group["hooks"]:
                    self.assertEqual(hook["type"], "command")
                    self.assertTrue(hook["async"])
                    script, state = hook["command"].rsplit(" ", 1)
                    self.assertEqual(script, f'"{glaide.HOOK_SCRIPT}"')
                    states.add(state)
        self.assertEqual(states, {"working", "done", "error", "attention"})


class ScreenshotTool(unittest.TestCase):
    def test_command_saves_to_the_given_path(self):
        command = glaide.screenshot_command("/tmp/x.png")
        if command is not None:                 # depends on what is installed
            self.assertIn("/tmp/x.png", command)


class HookScript(unittest.TestCase):
    """Runs hook.sh with a stand-in gdbus that records what it was asked to send."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = os.path.join(self.tmp.name, "sent")
        fake = os.path.join(self.tmp.name, "gdbus")
        with open(fake, "w") as f:
            f.write('#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done > "$GLAIDE_TEST_OUT"\n')
        os.chmod(fake, os.stat(fake).st_mode | stat.S_IXUSR)

    def tearDown(self):
        self.tmp.cleanup()

    def run_hook(self, state, payload, tab="3", bus="io.example.Drawer"):
        env = dict(os.environ, PATH=self.tmp.name + os.pathsep + os.environ["PATH"],
                   GLAIDE_TEST_OUT=self.out)
        env.pop("GLAIDE_TAB", None)
        env.pop("GLAIDE_BUS", None)
        if tab is not None:
            env["GLAIDE_TAB"] = tab
        if bus is not None:
            env["GLAIDE_BUS"] = bus
        text = payload if isinstance(payload, str) else json.dumps(payload)
        result = subprocess.run([os.path.join(ROOT, "hook.sh"), state], input=text,
                                capture_output=True, text=True, env=env)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        return glaide.read_text(self.out).splitlines() if os.path.exists(self.out) else None

    def message(self, state, payload):
        args = self.run_hook(state, payload)
        self.assertEqual(args[args.index("--dest") + 1], "io.example.Drawer")
        self.assertEqual(args[args.index("--object-path") + 1], "/io/example/Drawer")
        self.assertEqual(args[-3], "tab-state")
        self.assertTrue(args[-2].startswith("[<'") and args[-2].endswith("'>]"))
        return args[-2][3:-3]

    def test_nothing_is_sent_outside_a_drawer_tab(self):
        self.assertIsNone(self.run_hook("done", {}, tab=None))
        self.assertIsNone(self.run_hook("done", {}, bus=None))

    def test_subagent_events_are_ignored(self):
        self.assertIsNone(self.run_hook("done", {"agent_id": "abc", "last_assistant_message": "x"}))

    def test_working_carries_no_text(self):
        self.assertEqual(self.message("working", {"prompt": "secret question"}), "3:working:")

    def test_first_line_of_the_reply_without_markdown(self):
        reply = "\n\n## **The import** is `done`\n\nSecond paragraph."
        self.assertEqual(self.message("done", {"last_assistant_message": reply}),
                         "3:done:The import is done")

    def test_quotes_and_backslashes_are_escaped_for_gdbus(self):
        reply = "it's a C:\\path \"quoted\" åäö"
        self.assertEqual(self.message("done", {"last_assistant_message": reply}),
                         "3:done:it\\'s a C:\\\\path \"quoted\" åäö")

    def test_long_text_is_cut(self):
        text = self.message("done", {"last_assistant_message": "x" * 400}).split(":", 2)[2]
        self.assertEqual(len(text), 158)
        self.assertTrue(text.endswith("…"))

    def test_questions_and_permission_requests(self):
        ask = {"tool_name": "AskUserQuestion", "tool_input": {"questions": [{"question": "Which one?"}]}}
        self.assertEqual(self.message("attention", ask), "3:attention:Which one?")
        self.assertEqual(self.message("attention", {"tool_name": "ExitPlanMode", "tool_input": {}}),
                         "3:attention:Plan ready for your review")
        self.assertEqual(self.message("attention", {"message": "Claude needs your permission"}),
                         "3:attention:Claude needs your permission")

    def test_broken_input_still_reports_the_state(self):
        self.assertEqual(self.message("error", "not json"), "3:error:")


if __name__ == "__main__":
    unittest.main()
