"""
Tests for the layout-mode machinery in about_time.py (stack <-> row) — the
per-timer fixed box size, orientation-aware packing, and window fitting.

Unlike test_about_time.py, these need REAL Tkinter/customtkinter widgets —
fixed box sizing and packing orientation can't be verified without an actual
geometry manager computing real layout. This needs a real display (a local
Windows desktop session); it will not run headless/CI without a virtual
display. Windows are pushed off-screen (not withdrawn — withdraw can prevent
winfo_width from updating on some Tk builds) rather than hidden, so expect a
few windows to flash near (2000, 2000) during a run.

Strategy: same source-extraction approach as test_about_time.py (regex out
just the functions/class under test, exec into a controlled namespace) —
but this time the namespace's `root`/`timers_frame` are a real CTk() window
and CTkFrame, not mocks. Side-effecting globals unrelated to layout itself
(_save_settings, _update_layout_btn, the hover-tooltip) are stubbed no-ops,
so the *real* _toggle_layout_mode/_fit_window*/_pack_timer/_make_separator
code runs under test, just without touching disk or unrelated widgets.
"""

import json
import re
import types
from pathlib import Path

import customtkinter as ctk
import pytest

SRC_PATH = Path(__file__).parent.parent / "about_time.py"
SOURCE = SRC_PATH.read_text(encoding="utf-8")


def _extract(pattern):
    m = re.search(pattern, SOURCE, re.DOTALL | re.MULTILINE)
    assert m, f"pattern not found in about_time.py: {pattern!r}"
    return m.group(1)


def _build_namespace(root, timers_frame, timers):
    """A namespace with the real layout-mode code exec'd into it, wired to
    a real (but off-screen) root window and a fresh `timers` list."""
    _beep_calls = []
    _notify_calls = []
    ns = {
        "__builtins__": __builtins__,
        "re": re,
        "ctk": ctk,
        "root": root,
        "timers_frame": timers_frame,
        "timers": timers,
        "_layout_mode": "stack",
        "MAX_TIMERS": 5,
        "MAX_DURATION_SECONDS": 30 * 24 * 3600,
        "_TITLE_PLACEHOLDER": "Click to enter title",
        "_TITLE_PLACEHOLDER_COLOR": "#aaaaaa",
        "_TITLE_TEXT_COLOR": "#ffffff",
        # Unrelated side effects stubbed out — this suite is testing layout,
        # not settings persistence or the hover-tooltip widget.
        "_save_settings": lambda: None,
        "_update_layout_btn": lambda: None,
        "_layout_tip": types.SimpleNamespace(winfo_ismapped=lambda: False),
        "_update_mute_btn": lambda: None,
        "_restore_volume_if_silent": lambda: None,
        "_mute_tip": types.SimpleNamespace(winfo_ismapped=lambda: False),
        "_place_add_tile": lambda: None,
        "_muted": False,
        # _tick calls these on finish — recorded, not actually played/shown,
        # since this suite has no audio hardware or PowerShell dependency.
        "_beep_calls": _beep_calls,
        "beep": lambda *a, **k: _beep_calls.append(a),
        "_notify_calls": _notify_calls,
        "notify": lambda *a, **k: _notify_calls.append(a),
    }

    fmt_src = _extract(r"(^def fmt\(seconds\):.*?)(?=^\n)")
    parse_src = _extract(r"(^def parse_input\(text\):.*?)(?=^_FLASH_COLORS)")
    flash_src = _extract(r"(^_FLASH_COLORS = .*?)$")
    max_title_src = _extract(r"(^MAX_TITLE_LEN = \d+)$")
    btnw_src = _extract(r"(^BTN_W = \d+)$")
    timerw_src = _extract(r"(^TIMER_W_STACK = \d+\nTIMER_W_ROW = \d+)$")
    timerwfn_src = _extract(r"(^def _timer_w\(\):.*?)(?=^# ── Settings persistence)")
    timerh_src = _extract(r"(^TIMER_H = \d+)$")
    countdown_pt_src = _extract(r"(^COUNTDOWN_PT = \d+)")
    countdown_days_pt_src = _extract(r"(^COUNTDOWN_DAYS_PT = \d+)")
    make_tip_src = _extract(r"(^def _make_tip\(parent=None\):.*?)(?=^\n\n# ── Timer widget)")
    timerwidget_src = _extract(r"(^class TimerWidget.*?)(?=^# ── Root window)")
    make_sep_src = _extract(r"(^def _make_separator\(\):.*?)(?=^def _pack_timer)")
    pack_timer_src = _extract(r"(^def _pack_timer\(tw\):.*?)(?=^def add_timer)")
    add_timer_src = _extract(r"(^def add_timer\(.*?)(?=^def remove_timer)")
    remove_timer_src = _extract(r"(^def remove_timer\(tw\):.*?)(?=^def _fit_window\b)")
    fit_window_src = _extract(r"(^def _fit_window\(\):.*?)(?=^\n# ── Layout mode toggle)")
    relayout_src = _extract(r"(^def _relayout_timers\(\):.*?)(?=^def _toggle_layout_mode)")
    toggle_src = _extract(r"(^def _toggle_layout_mode\(\):.*?)(?=^\n# ── Add timer tile)")
    set_sound_enabled_src = _extract(r"(^def _set_sound_controls_enabled\(tw, enabled\):.*?)(?=^def toggle_mute)")
    toggle_mute_src = _extract(r"(^def toggle_mute\(\):.*?)(?=^def _update_mute_btn)")

    for src in (max_title_src, timerw_src, timerwfn_src, timerh_src, countdown_pt_src, countdown_days_pt_src, btnw_src, flash_src, fmt_src, parse_src,
                make_tip_src, timerwidget_src, make_sep_src, pack_timer_src,
                fit_window_src, relayout_src, toggle_src,
                set_sound_enabled_src, toggle_mute_src, add_timer_src, remove_timer_src):
        exec(src, ns)

    return ns


@pytest.fixture(scope="module")
def _root():
    """One real (off-screen) root window for the whole test module.

    customtkinter's global appearance/scaling trackers don't tolerate
    repeated CTk() creation/teardown within a single process well — reusing
    one root and only recreating timers_frame per test avoids that churn."""
    root = ctk.CTk()
    root.geometry("+2000+2000")  # off-screen, not withdrawn
    yield root
    root.destroy()


@pytest.fixture()
def env(_root):
    """Fresh timers_frame + empty timers list on the shared root, and the
    layout-mode namespace wired to them. Root's size constraints are reset
    between tests since _fit_window*/_toggle_layout_mode mutate them."""
    _root.wm_minsize(1, 1)
    _root.wm_maxsize(99999, 99999)
    _root.geometry("200x200")
    timers_frame = ctk.CTkFrame(_root, fg_color="transparent")
    timers_frame.pack(fill="x")
    timers = []
    ns = _build_namespace(_root, timers_frame, timers)
    yield ns
    timers_frame.destroy()


def _add_timer(ns, deletable=False, state="idle", sound="short", initial_title=""):
    """Thin wrapper around the real (extracted) add_timer — exercises the
    actual production packing/mute-on-open logic, not a re-implementation
    of it. Returns the newly added TimerWidget."""
    ns["add_timer"](deletable=deletable, initial_state=state, initial_sound=sound,
                    initial_title=initial_title)
    return ns["timers"][-1][1]


# ---------------------------------------------------------------------------
# Fixed box size
# ---------------------------------------------------------------------------

class TestFixedBoxSize:
    def test_single_timer_forced_to_timer_w_and_h(self, env):
        tw = _add_timer(env)
        env["root"].update_idletasks()
        assert tw.winfo_reqwidth() == env["_timer_w"]()
        assert tw.winfo_reqheight() == env["TIMER_H"]


    def test_paused_state_does_not_change_box_size(self, env):
        tw = _add_timer(env)
        env["root"].update_idletasks()
        tw._set_state("paused")
        env["root"].update_idletasks()
        assert tw.winfo_reqwidth() == env["_timer_w"]()
        assert tw.winfo_reqheight() == env["TIMER_H"]

    def test_deletable_timer_with_x_button_same_size(self, env):
        tw = _add_timer(env, deletable=True)
        env["root"].update_idletasks()
        assert tw.winfo_reqwidth() == env["_timer_w"]()
        assert tw.winfo_reqheight() == env["TIMER_H"]

    def test_paused_button_width_uses_timer_w_not_root_width(self, env):
        # Regression guard: this used to be computed from root.winfo_width(),
        # which broke the moment a timer box could be narrower than the
        # window (i.e. the moment row mode existed).
        tw = _add_timer(env)
        env["root"].update_idletasks()
        # Make the root window artificially very wide — if the paused-button
        # formula still used root width, this would blow the button width up.
        env["root"].geometry(f"{env['_timer_w']() * 5}x{env['TIMER_H']}")
        env["root"].update_idletasks()
        tw._set_state("paused")
        env["root"].update_idletasks()
        expected = max(40, (env["_timer_w"]() - 70) // 3)
        assert tw.stop_btn.cget("width") == expected
        assert tw.resume_btn.cget("width") == expected
        assert tw.restart_btn.cget("width") == expected


class TestPerLayoutWidth:
    """Row (sideways) timers are narrower than stacked ones."""

    def test_row_width_is_narrower_than_stack(self, env):
        assert env["TIMER_W_ROW"] == 210 and env["TIMER_W_STACK"] == 240

    def test_row_mode_timer_is_row_width(self, env):
        env["_layout_mode"] = "row"
        tw = _add_timer(env)
        env["root"].update_idletasks()
        assert tw.winfo_reqwidth() == env["TIMER_W_ROW"]

    def test_toggle_resizes_existing_timers_both_ways(self, env):
        tw = _add_timer(env)
        env["root"].update_idletasks()
        assert tw.winfo_reqwidth() == env["TIMER_W_STACK"]
        env["_toggle_layout_mode"]()
        env["root"].update_idletasks()
        assert tw.winfo_reqwidth() == env["TIMER_W_ROW"]
        env["_toggle_layout_mode"]()
        env["root"].update_idletasks()
        assert tw.winfo_reqwidth() == env["TIMER_W_STACK"]

    def test_paused_buttons_follow_the_layout_width(self, env):
        tw = _add_timer(env)
        tw._set_state("paused")
        env["_toggle_layout_mode"]()
        assert tw.stop_btn.cget("width") == max(40, (env["TIMER_W_ROW"] - 70) // 3)
        env["_toggle_layout_mode"]()
        assert tw.stop_btn.cget("width") == max(40, (env["TIMER_W_STACK"] - 70) // 3)

    def test_pack_timer_accepts_a_plain_frame_like_the_add_tile(self, env):
        """Regression: _place_add_tile packs a bare CTkFrame through _pack_timer;
        the first width change called TimerWidget-only code on it and crashed the
        app at startup (the suite stubs _place_add_tile, so only this catches it)."""
        for mode in ("stack", "row"):
            env["_layout_mode"] = mode
            tile = ctk.CTkFrame(env["timers_frame"], width=32, height=32)
            env["_pack_timer"](tile)
            tile.destroy()

    def test_widest_countdown_fits_row_width(self, env):
        """The 32d countdown is the floor that set the row width."""
        env["_layout_mode"] = "row"
        tw = _add_timer(env)
        tw.display_var.set("30d 23:59:59")
        env["root"].update_idletasks()
        need = tw.countdown_frame.winfo_reqwidth()
        assert need <= env["TIMER_W_ROW"], need


# ---------------------------------------------------------------------------
# Countdown edit rejection — a rejected edit must revert to what was on
# screen for that state, not silently reset to the full duration
# ---------------------------------------------------------------------------

class TestCommitCountdownRejection:
    def _reject_edit(self, tw, garbage="abc"):
        tw.editing_countdown = True
        tw.edit_var.set(garbage)
        tw._commit_countdown()

    def test_rejected_edit_on_paused_timer_shows_remaining_not_duration(self, env):
        tw = _add_timer(env)
        tw.duration_seconds = 900
        tw.remaining_seconds = 295
        tw.last_valid_display = env["fmt"](900)
        tw.display_var.set(env["fmt"](295))
        tw._set_state("paused")

        self._reject_edit(tw)

        assert tw.display_var.get() == env["fmt"](295)
        assert tw.remaining_seconds == 295  # untouched, only the display was wrong

    def test_rejected_edit_on_running_timer_shows_remaining(self, env):
        tw = _add_timer(env)
        tw.duration_seconds = 900
        tw.remaining_seconds = 800
        tw.display_var.set(env["fmt"](800))
        tw._set_state("running")

        self._reject_edit(tw)

        assert tw.display_var.get() == env["fmt"](800)

    def test_rejected_edit_on_idle_timer_shows_last_valid_display(self, env):
        tw = _add_timer(env)
        tw._set_state("idle")

        self._reject_edit(tw)

        assert tw.display_var.get() == tw.last_valid_display


# ---------------------------------------------------------------------------
# Orientation-aware packing
# ---------------------------------------------------------------------------

class TestPackTimerOrientation:
    def test_stack_mode_packs_top(self, env):
        tw = _add_timer(env)
        assert tw.pack_info()["side"] == "top"

    def test_row_mode_packs_left(self, env):
        env["_layout_mode"] = "row"
        tw = _add_timer(env)
        assert tw.pack_info()["side"] == "left"


class TestSeparatorOrientation:
    def test_stack_mode_separator_is_horizontal(self, env):
        _add_timer(env)
        sep = env["_make_separator"]()
        # a horizontal divider: explicit height, fills x, packed above/below
        assert sep.pack_info()["side"] == "top"
        assert int(sep.cget("height")) == 1

    def test_row_mode_separator_is_vertical(self, env):
        env["_layout_mode"] = "row"
        _add_timer(env)
        sep = env["_make_separator"]()
        assert sep.pack_info()["side"] == "left"
        assert int(sep.cget("width")) == 1


# ---------------------------------------------------------------------------
# Title entry length cap (A5) — a StringVar trace on the real entry widget,
# so typed and pasted text are both capped at MAX_TITLE_LEN.
# ---------------------------------------------------------------------------

class TestTitleEntryLimit:
    def test_pasted_overlong_title_is_cut_to_max(self, env):
        tw = _add_timer(env)
        tw.title_entry.delete(0, "end")
        tw.title_entry.insert(0, "x" * 250)  # an insert is what a paste does
        assert len(tw.title_entry.get()) == env["MAX_TITLE_LEN"] == 100

    def test_typing_past_the_cap_adds_nothing(self, env):
        tw = _add_timer(env)
        tw.title_entry.delete(0, "end")
        tw.title_entry.insert(0, "y" * 100)
        tw.title_entry.insert("end", "z")  # one more keystroke
        assert tw.title_entry.get() == "y" * 100

    def test_title_at_exactly_the_cap_is_kept_whole(self, env):
        tw = _add_timer(env)
        tw.title_entry.delete(0, "end")
        tw.title_entry.insert(0, "a" * 100)
        assert tw.title_entry.get() == "a" * 100

    def test_short_title_untouched(self, env):
        tw = _add_timer(env, initial_title="Bread")
        assert tw.title_entry.get() == "Bread"

    def test_placeholder_still_shown_for_untitled_timer(self, env):
        tw = _add_timer(env)
        assert tw.title_entry.get() == env["_TITLE_PLACEHOLDER"]


# ---------------------------------------------------------------------------
# Window fitting
# ---------------------------------------------------------------------------

class TestFitWindowRow:
    def test_locks_min_and_max_to_natural_size(self, env):
        env["_layout_mode"] = "row"
        _add_timer(env)
        _add_timer(env)
        env["_fit_window"]()
        root = env["root"]
        root.update()
        assert root.wm_minsize() == root.wm_maxsize()

    def test_width_grows_with_more_timers(self, env):
        env["_layout_mode"] = "row"
        _add_timer(env)
        env["_fit_window"]()
        env["root"].update()
        w1 = env["root"].winfo_width()

        _add_timer(env)
        env["_fit_window"]()
        env["root"].update()
        w2 = env["root"].winfo_width()

        assert w2 > w1

    def test_all_timers_shown_no_partial_reveal(self, env):
        # Both layout modes are fixed-size — no drag-to-collapse/partial-
        # reveal in either (v0.9.1 scope decision: stack mode used to allow
        # this, dropped as unwanted complexity from an earlier fix).
        env["_layout_mode"] = "row"
        for _ in range(3):
            _add_timer(env)
        env["_fit_window"]()
        env["root"].update()
        # width should fit all 3 boxes, not just one
        assert env["root"].winfo_width() >= env["_timer_w"]() * 3


class TestFitWindowStack:
    def test_locks_min_and_max_to_natural_size(self, env):
        _add_timer(env)
        _add_timer(env)
        env["_fit_window"]()
        root = env["root"]
        root.update()
        assert root.wm_minsize() == root.wm_maxsize()

    def test_height_grows_with_more_timers(self, env):
        _add_timer(env)
        env["_fit_window"]()
        env["root"].update()
        h1 = env["root"].winfo_height()

        _add_timer(env)
        env["_fit_window"]()
        env["root"].update()
        h2 = env["root"].winfo_height()

        assert h2 > h1


# ---------------------------------------------------------------------------
# Toggle — the width-carryover regression this feature actually hit live
# ---------------------------------------------------------------------------

class TestToggleLayoutMode:
    def test_toggle_flips_mode(self, env):
        _add_timer(env)
        env["_toggle_layout_mode"]()
        assert env["_layout_mode"] == "row"
        env["_toggle_layout_mode"]()
        assert env["_layout_mode"] == "stack"

    def test_toggle_to_row_repacks_left(self, env):
        tw = _add_timer(env)
        env["_toggle_layout_mode"]()
        assert tw.pack_info()["side"] == "left"

    def test_toggle_back_to_stack_repacks_top(self, env):
        tw = _add_timer(env)
        env["_toggle_layout_mode"]()  # -> row
        env["_toggle_layout_mode"]()  # -> stack
        assert tw.pack_info()["side"] == "top"

    def test_returning_to_stack_does_not_carry_over_row_width(self, env):
        """The actual bug caught during manual testing: after being in wide
        row mode, toggling back to stack left the window at row mode's wide
        width while applying stack mode's height — a giant mostly-empty box.
        _fit_window always recomputing its own required width (rather than
        preserving whatever the window's width happened to be) is what
        fixes this."""
        for _ in range(3):
            _add_timer(env)
        env["_toggle_layout_mode"]()  # -> row, now wide
        env["root"].update()
        row_width = env["root"].winfo_width()
        assert row_width > env["_timer_w"]() * 2  # sanity: genuinely wide

        env["_toggle_layout_mode"]()  # -> stack
        env["root"].update()
        stack_width = env["root"].winfo_width()

        # Stack mode is a single column — its natural width has nothing to
        # do with how many timers were shown side-by-side a moment ago.
        assert stack_width < row_width
        assert stack_width == env["root"].winfo_reqwidth()

    def test_toggle_preserves_timer_count(self, env):
        for _ in range(4):
            _add_timer(env)
        env["_toggle_layout_mode"]()
        env["_toggle_layout_mode"]()
        assert len(env["timers"]) == 4


# ---------------------------------------------------------------------------
# Global mute — a pure playback gate (see about_time.py's beep/toggle_mute).
# Each timer's own sound_mode is never touched by muting; only the sound
# buttons' enabled state changes, so there's nothing to restore on unmute
# and nothing lost if the app closes while still muted.
# ---------------------------------------------------------------------------

class TestToggleMute:
    def test_timer_already_off_stays_off_after_unmute(self, env):
        tw = _add_timer(env, sound=None)
        env["toggle_mute"]()
        env["toggle_mute"]()
        assert tw.sound_mode is None

    def test_toggle_mute_flips_flag(self, env):
        _add_timer(env)
        assert env["_muted"] is False
        env["toggle_mute"]()
        assert env["_muted"] is True
        env["toggle_mute"]()
        assert env["_muted"] is False

    def test_timer_opened_during_mute_keeps_its_own_sound(self, env):
        # a timer opened while globally muted still records its own choice —
        # muting is a playback gate, not a data mutation — it just can't be
        # heard (nor its buttons touched) until unmuted
        _add_timer(env, sound="short")
        env["toggle_mute"]()
        new_tw = _add_timer(env, sound="long")
        assert new_tw.sound_mode == "long"

    def test_timer_opened_during_mute_has_disabled_sound_buttons(self, env):
        env["toggle_mute"]()
        new_tw = _add_timer(env, sound="short")
        for btn in new_tw._sound_btns.values():
            assert btn.cget("state") == "disabled"

    # -- Sound buttons locked while muted (user-decided fix for the "one
    #    timer quietly un-muted while global mute still shows on" edge case) --


    def test_sound_buttons_start_enabled_when_not_muted(self, env):
        tw = _add_timer(env, sound="short")
        for btn in tw._sound_btns.values():
            assert btn.cget("state") == "normal"

    def test_toggle_sound_is_a_no_op_while_muted(self, env):
        # logic-level guard, not just the disabled button — calling it
        # directly (bypassing the UI) must still refuse to change the choice
        tw = _add_timer(env, sound="short")
        env["toggle_mute"]()
        tw._toggle_sound("medium")
        assert tw.sound_mode == "short"

    def test_notify_bell_stays_enabled_while_muted(self, env):
        # mute is sound-only — a timer's own notification toggle is
        # unrelated and must not be locked by it
        tw = _add_timer(env, sound="short")
        env["toggle_mute"]()
        assert tw.notify_btn.cget("state") == "normal"
        tw._toggle_notify()
        assert tw.notify_enabled is True

    # -- Mute/unmute doesn't disturb anything else about a timer's state --

    # -- Grey-out holds on every route into mute (button, right-click, slider to 0) --

    def test_set_muted_true_greys_sound_icons_but_not_the_bell(self, env):
        tw = _add_timer(env, sound="short")
        env["_set_muted"](True)
        assert env["_muted"] is True
        for btn in tw._sound_btns.values():
            assert btn.cget("state") == "disabled"
        assert tw.notify_btn.cget("state") == "normal"

    def test_set_muted_false_restores_the_sound_icons(self, env):
        tw = _add_timer(env, sound="short")
        env["_set_muted"](True)
        env["_set_muted"](False)
        for btn in tw._sound_btns.values():
            assert btn.cget("state") == "normal"

    def test_set_muted_to_the_same_value_does_nothing(self, env):
        tw = _add_timer(env, sound="short")
        before = {k: b.cget("state") for k, b in tw._sound_btns.items()}
        env["_set_muted"](False)
        assert {k: b.cget("state") for k, b in tw._sound_btns.items()} == before

    def test_unmuting_asks_for_the_volume_check(self, env):
        seen = []
        env["_restore_volume_if_silent"] = lambda: seen.append(1)
        _add_timer(env, sound="short")
        env["_set_muted"](True)
        assert seen == []
        env["_set_muted"](False)
        assert seen == [1]

    def test_mute_does_not_change_duration_or_remaining(self, env):
        tw = _add_timer(env, sound="short")
        tw.duration_seconds = 1234
        tw.remaining_seconds = 999
        env["toggle_mute"]()
        env["toggle_mute"]()
        assert tw.duration_seconds == 1234
        assert tw.remaining_seconds == 999

    def test_mute_does_not_change_running_state(self, env):
        tw = _add_timer(env, sound="short")
        tw._set_state("running")
        env["toggle_mute"]()
        assert tw.state == "running"
        env["toggle_mute"]()
        assert tw.state == "running"

    def test_mute_does_not_change_notify_enabled(self, env):
        tw = _add_timer(env, sound="short")
        tw.notify_enabled = True
        env["toggle_mute"]()
        env["toggle_mute"]()
        assert tw.notify_enabled is True

    def test_mute_does_not_change_title(self, env):
        tw = _add_timer(env, sound="short")
        tw.title_entry.delete(0, "end")
        tw.title_entry.insert(0, "Deadline")
        env["toggle_mute"]()
        env["toggle_mute"]()
        assert tw.title_entry.get() == "Deadline"

    # -- Combinatorial: every mix of sound choices across 1-5 timers survives
    #    a mute/unmute round-trip untouched --

    @pytest.mark.parametrize("sounds", [
        ("short",),
        (None, None, None, None, None),
        ("medium", None, "short", None, "long"),
    ])
    def test_mute_then_unmute_leaves_exact_combination_unchanged(self, env, sounds):
        widgets = [_add_timer(env, sound=s) for s in sounds]

        env["toggle_mute"]()
        assert [tw.sound_mode for tw in widgets] == list(sounds)

        env["toggle_mute"]()
        assert [tw.sound_mode for tw in widgets] == list(sounds)

    @pytest.mark.parametrize("count", [1, 3])
    def test_mute_disables_exactly_n_timers_sound_buttons(self, env, count):
        widgets = [_add_timer(env, sound="short") for _ in range(count)]
        env["toggle_mute"]()
        assert len(env["timers"]) == count
        for tw in widgets:
            for btn in tw._sound_btns.values():
                assert btn.cget("state") == "disabled"


# ---------------------------------------------------------------------------
# remove_timer — list/widget bookkeeping. _save_settings stays the shared
# no-op stub here (its own correctness is covered separately below), so this
# suite only verifies remove_timer's own responsibility.
# ---------------------------------------------------------------------------

class TestRemoveTimer:
    def test_removes_the_targeted_widget_only(self, env):
        a = _add_timer(env)
        b = _add_timer(env)
        c = _add_timer(env)

        env["remove_timer"](b)

        assert [tw for (_sep, tw) in env["timers"]] == [a, c]

    def test_decrements_timer_count(self, env):
        for _ in range(3):
            _add_timer(env)
        tw = env["timers"][0][1]

        env["remove_timer"](tw)

        assert len(env["timers"]) == 2

    def test_cancels_pending_after_callback(self, env):
        tw = _add_timer(env)
        tw._set_state("running")
        tw.after_id = env["root"].after(60_000, lambda: None)

        # No exception = after_cancel was reached; a stale/expired after_id
        # would raise from Tk if remove_timer skipped cancelling it.
        env["remove_timer"](tw)

    def test_removing_first_timer_leaves_remaining_separators_consistent(self, env):
        # Regression guard: each timer after the first owns a separator
        # ABOVE it (see _pack_timer/_make_separator) — removing timer 0
        # must not leave timer 1's separator orphaned or duplicate one.
        a = _add_timer(env)
        b = _add_timer(env)

        env["remove_timer"](a)

        assert len(env["timers"]) == 1
        remaining_sep, remaining_tw = env["timers"][0]
        assert remaining_tw is b


# ---------------------------------------------------------------------------
# TimerWidget._tick — the actual countdown/finish logic. beep()/notify() are
# recorded via the namespace stubs (_beep_calls/_notify_calls), never really
# played/shown, so this suite has no audio or PowerShell dependency.
# ---------------------------------------------------------------------------

class TestTick:
    def test_running_timer_decrements_and_reschedules(self, env):
        tw = _add_timer(env)
        tw.duration_seconds = 100
        tw.remaining_seconds = 100
        tw._set_state("running")

        tw._tick()

        assert tw.remaining_seconds == 99
        assert tw.display_var.get() == env["fmt"](99)
        assert tw.after_id is not None
        env["root"].after_cancel(tw.after_id)

    def test_reaching_zero_finishes_and_beeps(self, env):
        tw = _add_timer(env, sound="short")
        tw.duration_seconds = 1
        tw.remaining_seconds = 1
        tw._set_state("running")

        tw._tick()

        assert tw.remaining_seconds == 0
        assert tw.state == "finished"
        assert tw.display_var.get() == "Done!"
        assert tw.after_id is None
        assert env["_beep_calls"] == [("short",)]

    def test_finish_does_not_notify_when_notify_disabled(self, env):
        tw = _add_timer(env)
        tw.notify_enabled = False
        tw.duration_seconds = 1
        tw.remaining_seconds = 1
        tw._set_state("running")

        tw._tick()

        assert env["_notify_calls"] == []

    def test_finish_notifies_with_title_when_notify_enabled(self, env):
        tw = _add_timer(env, initial_title="Pizza")
        tw.notify_enabled = True
        tw.duration_seconds = 1
        tw.remaining_seconds = 1
        tw._set_state("running")

        tw._tick()

        assert len(env["_notify_calls"]) == 1
        title, duration = env["_notify_calls"][0]
        assert title == "Pizza"

    def test_finish_notifies_with_empty_title_when_untitled(self, env):
        tw = _add_timer(env)
        tw.notify_enabled = True
        tw.duration_seconds = 1
        tw.remaining_seconds = 1
        tw._set_state("running")

        tw._tick()

        title, duration = env["_notify_calls"][0]
        assert title == ""

    def test_tick_on_non_running_timer_is_a_noop(self, env):
        # Guards a stale scheduled callback: if the timer was paused/stopped
        # between being scheduled and firing, _tick must not still count
        # down or re-schedule itself.
        tw = _add_timer(env)
        tw.duration_seconds = 100
        tw.remaining_seconds = 100
        tw._set_state("paused")
        tw.after_id = "stale"

        tw._tick()

        assert tw.remaining_seconds == 100
        assert tw.after_id is None


# ---------------------------------------------------------------------------
# _commit_countdown — happy path (valid input accepted). Rejection paths are
# covered by TestCommitCountdownRejection above.
# ---------------------------------------------------------------------------

class TestCommitCountdownAcceptance:
    def _accept_edit(self, tw, text):
        tw.editing_countdown = True
        tw.edit_var.set(text)
        tw._commit_countdown()

    def test_valid_edit_on_idle_timer_arms_and_starts_it(self, env):
        # Confirmed intentional design: committing a valid duration is what
        # "arms" a timer, even from idle — see the paused-edit-rejection and
        # double-tick-chain backlog discussions.
        tw = _add_timer(env)

        self._accept_edit(tw, "5:00")

        assert tw.duration_seconds == 300
        assert tw.remaining_seconds == 300
        assert tw.state == "running"
        assert tw.after_id is not None
        env["root"].after_cancel(tw.after_id)

    def test_valid_edit_on_paused_timer_restarts_from_new_duration(self, env):
        tw = _add_timer(env)
        tw.duration_seconds = 900
        tw.remaining_seconds = 300
        tw._set_state("paused")

        self._accept_edit(tw, "2:00")

        assert tw.duration_seconds == 120
        assert tw.remaining_seconds == 120
        assert tw.state == "running"
        env["root"].after_cancel(tw.after_id)

    def test_valid_edit_cancels_previous_after_callback(self, env):
        # Regression guard for the double-tick-chain concern: editing a
        # running timer's duration must not leave the old countdown chain
        # alive alongside the new one.
        tw = _add_timer(env)
        tw._set_state("running")
        stale_after_id = tw.after_id

        self._accept_edit(tw, "10:00")

        assert tw.after_id != stale_after_id
        env["root"].after_cancel(tw.after_id)


# ---------------------------------------------------------------------------
# _save_settings — the actual on-disk persistence. Uses its own namespace
# with the real function exec'd in (every other suite above keeps the
# shared no-op stub, to isolate what they're each actually testing).
# ---------------------------------------------------------------------------

def _build_save_settings_namespace(ns, settings_path):
    save_src = re.search(
        r"(^def _save_settings\(\):.*?)(?=^\n_s = _load_settings)",
        SOURCE, re.DOTALL | re.MULTILINE,
    ).group(1)
    # Helpers _save_settings calls: title cap, size caps and the error logger
    # (they sit together between the settings constants and the backup helper).
    helpers_src = _extract(r"(^MAX_TITLE_LEN = .*?)(?=^def _backup_corrupt_settings)")
    ns["_SETTINGS_PATH"] = str(settings_path)
    ns["topmost_var"] = ctk.BooleanVar(value=False)
    ns["json"] = __import__("json")
    ns["os"] = __import__("os")
    ns["sys"] = __import__("sys")
    exec(helpers_src, ns)
    exec(save_src, ns)
    return ns


class TestSaveSettings:
    def test_writes_pinned_layout_and_muted(self, env, tmp_path):
        _build_save_settings_namespace(env, tmp_path / "settings.json")
        env["topmost_var"].set(True)
        env["_layout_mode"] = "row"
        env["_muted"] = True

        env["_save_settings"]()

        data = json.loads((tmp_path / "settings.json").read_text())
        assert data["pinned"] is True
        assert data["layout_mode"] == "row"
        assert data["muted"] is True

    def test_writes_each_timers_fields(self, env, tmp_path):
        _build_save_settings_namespace(env, tmp_path / "settings.json")
        tw = _add_timer(env, initial_title="Bread", sound="long")
        tw.duration_seconds = 600
        tw.remaining_seconds = 450
        tw.notify_enabled = True

        env["_save_settings"]()

        data = json.loads((tmp_path / "settings.json").read_text())
        saved = data["timers"][0]
        assert saved["title"] == "Bread"
        assert saved["duration"] == 600
        assert saved["remaining"] == 450
        assert saved["sound"] == "long"
        assert saved["notify"] is True

    def test_placeholder_title_saved_as_empty_string(self, env, tmp_path):
        # Regression guard: the literal placeholder text ("Click to enter
        # title") must never be written as if it were a real user title.
        _build_save_settings_namespace(env, tmp_path / "settings.json")
        _add_timer(env)  # untitled -> shows the placeholder

        env["_save_settings"]()

        data = json.loads((tmp_path / "settings.json").read_text())
        assert data["timers"][0]["title"] == ""

    def test_overlong_title_is_clipped_to_max_title_len_on_save(self, env, tmp_path):
        _build_save_settings_namespace(env, tmp_path / "settings.json")
        _add_timer(env, initial_title="x" * 250)

        env["_save_settings"]()

        data = json.loads((tmp_path / "settings.json").read_text())
        assert len(data["timers"][0]["title"]) == env["MAX_TITLE_LEN"] == 100

    def test_save_is_skipped_while_writes_are_blocked(self, env, tmp_path):
        # A2: settings.json couldn't be read or backed up at load, so saving
        # must not touch it (the original may be valid behind a lock).
        settings = tmp_path / "settings.json"
        settings.write_text("original")
        _build_save_settings_namespace(env, settings)
        env["_settings_write_blocked"] = True

        env["_save_settings"]()

        assert settings.read_text() == "original"
        assert not (tmp_path / "settings.json.tmp").exists()

    def test_save_still_writes_when_not_blocked(self, env, tmp_path):
        settings = tmp_path / "settings.json"
        _build_save_settings_namespace(env, settings)
        assert env["_settings_write_blocked"] is False

        env["_save_settings"]()

        assert settings.exists()

    def test_write_is_atomic_no_leftover_tmp_file(self, env, tmp_path):
        _build_save_settings_namespace(env, tmp_path / "settings.json")

        env["_save_settings"]()

        assert (tmp_path / "settings.json").exists()
        assert not (tmp_path / "settings.json.tmp").exists()

    def test_write_failure_does_not_raise(self, env, tmp_path):
        # Point at a path whose parent can never be created (a file, not a
        # directory, sitting where a directory component is expected) —
        # _save_settings must catch and log, not propagate.
        blocker = tmp_path / "not_a_directory"
        blocker.write_text("x")
        bad_path = blocker / "nested" / "settings.json"
        _build_save_settings_namespace(env, bad_path)

        env["_save_settings"]()  # must not raise
