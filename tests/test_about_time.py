"""
Tests for about_time.py — pure/near-pure functions only.

Strategy: extract _load_settings, parse_input, and fmt by exec-ing only the
lines that define them, with all platform/tkinter dependencies already mocked
in sys.modules.  The full module is never imported so tkinter and CTk are
never instantiated.
"""

import importlib
import json
import math
import os
import re
import struct
import sys
import types
import unittest.mock as mock
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers — build a minimal namespace containing the pure functions
# ---------------------------------------------------------------------------

def _make_namespace(settings_path: str) -> dict:
    """
    Exec the minimal subset of about_time.py needed for the three testable
    functions: _load_settings, parse_input, fmt.

    We set _SETTINGS_PATH to a caller-controlled path so file-I/O tests are
    fully isolated via tmp_path.
    """
    ns = {
        "__builtins__": __builtins__,
        "json": json,
        "os": os,
        "sys": sys,
        "re": re,
        "math": math,
        "struct": struct,
        "_SETTINGS_PATH": settings_path,
        "MAX_TIMERS": 5,
        "MAX_DURATION_SECONDS": 32 * 24 * 3600,
    }

    src_path = Path(__file__).parent.parent / "about_time.py"
    source = src_path.read_text(encoding="utf-8")

    # Extract _is_int ────────────────────────────────────────────────────────
    is_int_match = re.search(
        r"(^def _is_int\(value\):.*?)(?=^def _load_settings)",
        source,
        re.DOTALL | re.MULTILINE,
    )
    assert is_int_match, "_is_int not found in source"

    # Extract _load_settings ─────────────────────────────────────────────────
    # Runs from the "def _load_settings" line through the blank line before
    # "def _save_settings".
    load_match = re.search(
        r"(^def _load_settings\(\):.*?)(?=^def _save_settings)",
        source,
        re.DOTALL | re.MULTILINE,
    )
    assert load_match, "_load_settings not found in source"

    # Extract parse_input ────────────────────────────────────────────────────
    parse_match = re.search(
        r"(^def parse_input\(text\):.*?)(?=^_FLASH_COLORS)",
        source,
        re.DOTALL | re.MULTILINE,
    )
    assert parse_match, "parse_input not found in source"

    # Extract fmt ────────────────────────────────────────────────────────────
    fmt_match = re.search(
        r"(^def fmt\(seconds\):.*?)(?=^\n)",
        source,
        re.DOTALL | re.MULTILINE,
    )
    assert fmt_match, "fmt not found in source"

    exec(fmt_match.group(1), ns)
    exec(is_int_match.group(1), ns)
    exec(load_match.group(1), ns)
    exec(parse_match.group(1), ns)

    return ns


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def ns(tmp_path):
    """Namespace with functions wired to a temp settings file."""
    settings_file = tmp_path / "settings.json"
    return _make_namespace(str(settings_file)), settings_file


# Convenience aliases so individual tests stay readable
@pytest.fixture()
def load(ns):
    namespace, settings_file = ns
    return namespace["_load_settings"], settings_file


@pytest.fixture()
def parse():
    # parse_input has no I/O; wire to a non-existent path (never read)
    ns = _make_namespace("/nonexistent/settings.json")
    return ns["parse_input"]


@pytest.fixture()
def fmt_fn():
    ns = _make_namespace("/nonexistent/settings.json")
    return ns["fmt"]


# ---------------------------------------------------------------------------
# _load_settings — missing / corrupt / partial / invalid / migration
# ---------------------------------------------------------------------------

REQUIRED_KEYS = {"pinned", "layout_mode", "muted",
                 "window_x", "window_y", "timers"}

DEFAULTS = {
    "pinned": False, "layout_mode": "stack",
    "window_x": None, "window_y": None, "timers": [],
}


class TestLoadSettingsMissingFile:
    def test_returns_complete_defaults_when_file_absent(self, load):
        fn, path = load
        assert not path.exists()
        result = fn()
        assert result.keys() == REQUIRED_KEYS

    def test_timers_default_is_empty_list(self, load):
        fn, _ = load
        assert fn()["timers"] == []

    def test_window_coords_default_none(self, load):
        fn, _ = load
        r = fn()
        assert r["window_x"] is None
        assert r["window_y"] is None

    def test_pinned_default_false(self, load):
        fn, _ = load
        assert fn()["pinned"] is False


class TestLoadSettingsCorruptFile:
    def test_corrupt_json_returns_complete_defaults(self, load):
        fn, path = load
        path.write_text("{ this is not json }", encoding="utf-8")
        result = fn()
        assert result.keys() == REQUIRED_KEYS
        # All defaults intact
        assert result["timers"] == []
        assert result["window_x"] is None

    def test_empty_file_returns_defaults(self, load):
        fn, path = load
        path.write_text("", encoding="utf-8")
        result = fn()
        assert result.keys() == REQUIRED_KEYS


class TestLoadSettingsHappyPath:
    def _write(self, path, data):
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_valid_full_settings_round_trips(self, load):
        fn, path = load
        data = {
            "pinned": True,
            "window_x": 100, "window_y": 200,
            "timers": [{"title": "Pomodoro", "duration": 1500,
                        "remaining": 1500, "state": "idle",
                        "sound": "medium", "notify": True}],
        }
        self._write(path, data)
        r = fn()
        assert r["pinned"] is True
        assert r["window_x"] == 100
        assert r["window_y"] == 200
        assert len(r["timers"]) == 1
        assert r["timers"][0]["title"] == "Pomodoro"
        assert r["timers"][0]["duration"] == 1500
        assert r["timers"][0]["sound"] == "medium"
        assert r["timers"][0]["notify"] is True

    def test_pinned_true(self, load):
        fn, path = load
        self._write(path, {"pinned": True})
        r = fn()
        assert r["pinned"] is True

    def test_layout_mode_row_round_trips(self, load):
        fn, path = load
        self._write(path, {"layout_mode": "row"})
        r = fn()
        assert r["layout_mode"] == "row"

    def test_layout_mode_stack_round_trips(self, load):
        fn, path = load
        self._write(path, {"layout_mode": "stack"})
        r = fn()
        assert r["layout_mode"] == "stack"

    def test_muted_true_round_trips(self, load):
        fn, path = load
        self._write(path, {"muted": True})
        r = fn()
        assert r["muted"] is True

    def test_muted_false_round_trips(self, load):
        fn, path = load
        self._write(path, {"muted": False})
        r = fn()
        assert r["muted"] is False

    def test_muted_defaults_false_when_absent(self, load):
        fn, path = load
        self._write(path, {"pinned": True})
        r = fn()
        assert r["muted"] is False


class TestLoadSettingsLayoutMode:
    def _write(self, path, data):
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_missing_layout_mode_defaults_to_stack(self, load):
        fn, path = load
        self._write(path, {"volume": 50})
        r = fn()
        assert r["layout_mode"] == "stack"

    def test_invalid_layout_mode_falls_back_to_stack(self, load):
        fn, path = load
        self._write(path, {"layout_mode": "diagonal"})
        r = fn()
        assert r["layout_mode"] == "stack"

    def test_null_layout_mode_falls_back_to_stack(self, load):
        fn, path = load
        self._write(path, {"layout_mode": None})
        r = fn()
        assert r["layout_mode"] == "stack"

    def test_numeric_layout_mode_falls_back_to_stack(self, load):
        fn, path = load
        self._write(path, {"layout_mode": 1})
        r = fn()
        assert r["layout_mode"] == "stack"


class TestLoadSettingsInvalidFieldValues:
    def _write(self, path, data):
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_window_coords_float_clears_both(self, load):
        fn, path = load
        self._write(path, {"window_x": 1.5, "window_y": 200})
        r = fn()
        assert r["window_x"] is None
        assert r["window_y"] is None

    def test_window_coords_string_clears_both(self, load):
        fn, path = load
        self._write(path, {"window_x": "left", "window_y": 200})
        r = fn()
        assert r["window_x"] is None
        assert r["window_y"] is None

    def test_window_coords_none_stays_none(self, load):
        fn, path = load
        self._write(path, {"window_x": None, "window_y": None})
        r = fn()
        assert r["window_x"] is None
        assert r["window_y"] is None

    def test_window_coords_bool_clears_both(self, load):
        # Regression guard: bool is an int subclass in Python, so a
        # hand-edited/corrupt settings.json with `"window_x": true` would
        # otherwise pass a plain isinstance(x, int) check and get silently
        # treated as window_x=1.
        fn, path = load
        self._write(path, {"window_x": True, "window_y": 200})
        r = fn()
        assert r["window_x"] is None
        assert r["window_y"] is None

    def test_timer_bool_duration_gets_default(self, load):
        fn, path = load
        self._write(path, {"timers": [{"title": "Bad", "duration": True,
                                        "remaining": 900, "state": "idle"}]})
        r = fn()
        assert r["timers"][0]["duration"] == 15 * 60

    def test_timer_bool_remaining_falls_back_to_duration(self, load):
        fn, path = load
        self._write(path, {"timers": [{"title": "Bad", "duration": 900,
                                        "remaining": False, "state": "idle"}]})
        r = fn()
        assert r["timers"][0]["remaining"] == 900

    def test_timer_invalid_duration_gets_default(self, load):
        fn, path = load
        self._write(path, {"timers": [{"title": "Bad", "duration": -1,
                                        "remaining": 900, "state": "idle"}]})
        r = fn()
        assert r["timers"][0]["duration"] == 15 * 60

    def test_timer_duration_zero_gets_default(self, load):
        fn, path = load
        self._write(path, {"timers": [{"duration": 0}]})
        r = fn()
        assert r["timers"][0]["duration"] == 15 * 60

    def test_timer_duration_max_boundary_ok(self, load):
        fn, path = load
        self._write(path, {"timers": [{"duration": 32 * 24 * 3600}]})
        r = fn()
        assert r["timers"][0]["duration"] == 32 * 24 * 3600

    def test_timer_duration_over_max_gets_default(self, load):
        fn, path = load
        self._write(path, {"timers": [{"duration": 32 * 24 * 3600 + 1}]})
        r = fn()
        assert r["timers"][0]["duration"] == 15 * 60

    def test_timer_duration_old_cap_no_longer_a_limit(self, load):
        # 359999 (the old 99:59:59 cap) is well within the new 32-day cap
        fn, path = load
        self._write(path, {"timers": [{"duration": 359999}]})
        r = fn()
        assert r["timers"][0]["duration"] == 359999

    def test_timer_invalid_state_gets_idle(self, load):
        fn, path = load
        self._write(path, {"timers": [{"duration": 900, "state": "exploding"}]})
        r = fn()
        assert r["timers"][0]["state"] == "idle"

    def test_timer_sound_short_kept(self, load):
        fn, path = load
        self._write(path, {"timers": [{"duration": 900, "sound": "short"}]})
        r = fn()
        assert r["timers"][0]["sound"] == "short"

    def test_timer_sound_medium_kept(self, load):
        fn, path = load
        self._write(path, {"timers": [{"duration": 900, "sound": "medium"}]})
        r = fn()
        assert r["timers"][0]["sound"] == "medium"

    def test_timer_sound_long_kept(self, load):
        fn, path = load
        self._write(path, {"timers": [{"duration": 900, "sound": "long"}]})
        r = fn()
        assert r["timers"][0]["sound"] == "long"

    def test_timer_sound_explicit_null_stays_off(self, load):
        # explicitly present as null = the timer's sound was deliberately
        # turned off (toggle-off), not "value missing"
        fn, path = load
        self._write(path, {"timers": [{"duration": 900, "sound": None}]})
        r = fn()
        assert r["timers"][0]["sound"] is None

    def test_timer_sound_invalid_value_falls_back(self, load):
        fn, path = load
        self._write(path, {"timers": [{"duration": 900, "sound": "mute"}]})
        r = fn()
        assert r["timers"][0]["sound"] == "short"

    def test_timer_sound_missing_key_defaults_short(self, load):
        # key absent entirely (not present, not null) = pre-per-timer-sound
        # file, or a freshly-created timer — defaults to "short"
        fn, path = load
        self._write(path, {"timers": [{"duration": 900}]})
        r = fn()
        assert r["timers"][0]["sound"] == "short"

    def test_timer_notify_true_kept(self, load):
        fn, path = load
        self._write(path, {"timers": [{"duration": 900, "notify": True}]})
        r = fn()
        assert r["timers"][0]["notify"] is True

    def test_timer_notify_missing_key_defaults_false(self, load):
        fn, path = load
        self._write(path, {"timers": [{"duration": 900}]})
        r = fn()
        assert r["timers"][0]["notify"] is False

    def test_timer_notify_invalid_type_falls_back(self, load):
        fn, path = load
        self._write(path, {"timers": [{"duration": 900, "notify": "yes"}]})
        r = fn()
        assert r["timers"][0]["notify"] is False

    def test_timer_remaining_over_duration_gets_duration(self, load):
        fn, path = load
        self._write(path, {"timers": [{"duration": 900, "remaining": 9999,
                                        "state": "idle"}]})
        r = fn()
        assert r["timers"][0]["remaining"] == 900

    def test_timer_remaining_negative_gets_duration(self, load):
        fn, path = load
        self._write(path, {"timers": [{"duration": 900, "remaining": -1,
                                        "state": "idle"}]})
        r = fn()
        assert r["timers"][0]["remaining"] == 900

    def test_non_dict_timer_entries_skipped(self, load):
        fn, path = load
        self._write(path, {"timers": ["not a dict", {"duration": 900}, None]})
        r = fn()
        # Only the dict entry survives
        assert len(r["timers"]) == 1
        assert r["timers"][0]["duration"] == 900

    def test_timers_capped_at_max(self, load):
        fn, path = load
        timers = [{"duration": 60 * i, "title": f"T{i}", "remaining": 60 * i,
                   "state": "idle"} for i in range(1, 10)]
        # duration 0 is invalid so use 60..540
        timers = [{"duration": 60 * (i + 1), "title": f"T{i}",
                   "remaining": 60 * (i + 1), "state": "idle"} for i in range(9)]
        self._write(path, {"timers": timers})
        r = fn()
        assert len(r["timers"]) <= 5

    def test_timer_non_string_title_becomes_empty(self, load):
        fn, path = load
        self._write(path, {"timers": [{"title": 42, "duration": 900}]})
        r = fn()
        assert r["timers"][0]["title"] == ""


class TestLoadSettingsHardening:
    def test_long_title_clipped_to_100_chars(self, load):
        fn, f = load
        f.write_text(json.dumps({"timers": [{"title": "x" * 3000}]}))
        assert fn()["timers"][0]["title"] == "x" * 100

    def test_legacy_title_clipped_too(self, load):
        fn, f = load
        f.write_text(json.dumps({"titles": ["y" * 500]}))
        assert fn()["timers"][0]["title"] == "y" * 100

    def test_oversize_file_rejected_not_parsed(self, load):
        fn, f = load
        f.write_text(json.dumps({"timers": [{"title": "ok"}], "pad": "z" * (300 * 1024)}))
        result = fn()
        assert result["timers"] == []
        assert f.with_name("settings.json.bak").exists()

    def test_corrupt_file_kept_as_bak(self, load):
        fn, f = load
        f.write_text("{not json")
        fn()
        bak = f.with_name("settings.json.bak")
        assert bak.read_text() == "{not json"
        assert not f.exists()

    def test_non_object_root_treated_as_corrupt(self, load):
        fn, f = load
        f.write_text("[1, 2, 3]")
        assert fn()["timers"] == []
        assert f.with_name("settings.json.bak").exists()

    def test_missing_file_makes_no_bak_or_log(self, load):
        fn, f = load
        fn()
        assert not f.with_name("settings.json.bak").exists()
        assert not f.with_name("error.log").exists()

    def test_failure_written_to_error_log(self, load):
        fn, f = load
        f.write_text("{not json")
        fn()
        assert "_load_settings failed" in f.with_name("error.log").read_text()


class TestLogErrorCap:
    def test_log_rotated_when_over_cap(self, ns):
        namespace, f = ns
        log = f.with_name("error.log")
        log.write_text("a" * (namespace["_MAX_LOG_BYTES"] + 10))
        namespace["_log_error"]("fresh")
        assert log.read_text().strip().endswith(" fresh")
        assert log.with_name("error.log.old").exists()


class TestLoadSettingsLegacyTitlesMigration:
    def _write(self, path, data):
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_legacy_titles_list_migrated_to_timers(self, load):
        fn, path = load
        self._write(path, {"titles": ["Morning focus", "Pomodoro"]})
        r = fn()
        assert len(r["timers"]) == 2
        assert r["timers"][0]["title"] == "Morning focus"
        assert r["timers"][1]["title"] == "Pomodoro"

    def test_legacy_titles_get_default_duration(self, load):
        fn, path = load
        self._write(path, {"titles": ["Sprint"]})
        r = fn()
        assert r["timers"][0]["duration"] == 15 * 60

    def test_legacy_non_string_titles_become_empty(self, load):
        fn, path = load
        self._write(path, {"titles": [42, None, "Valid"]})
        r = fn()
        assert r["timers"][0]["title"] == ""
        assert r["timers"][1]["title"] == ""
        assert r["timers"][2]["title"] == "Valid"

    def test_legacy_empty_titles_list_gives_one_default_timer(self, load):
        fn, path = load
        self._write(path, {"titles": []})
        r = fn()
        # Empty list → titles or [""] → one timer with empty title
        assert len(r["timers"]) == 1
        assert r["timers"][0]["title"] == ""

    def test_timers_key_takes_precedence_over_titles(self, load):
        fn, path = load
        self._write(path, {
            "titles": ["Old title"],
            "timers": [{"title": "New title", "duration": 900,
                        "remaining": 900, "state": "idle"}],
        })
        r = fn()
        assert len(r["timers"]) == 1
        assert r["timers"][0]["title"] == "New title"

    def test_null_titles_falls_back_to_one_empty_timer(self, load):
        fn, path = load
        self._write(path, {"titles": None})
        r = fn()
        assert len(r["timers"]) == 1
        assert r["timers"][0]["title"] == ""


class TestLoadSettingsSoundNotifyMigration:
    """Pre-v0.9 files had a single global "sound"/"notifications" pair, not
    per-timer values, and no global mute concept at all — mute was just one
    of the four values "sound" could be. These tests cover the one-time
    upgrade path."""

    def _write(self, path, data):
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_old_global_sound_becomes_timer_default(self, load):
        fn, path = load
        self._write(path, {
            "sound": "long",
            "timers": [{"title": "A", "duration": 900}, {"title": "B", "duration": 900}],
        })
        r = fn()
        assert r["timers"][0]["sound"] == "long"
        assert r["timers"][1]["sound"] == "long"

    def test_old_global_notifications_becomes_timer_default(self, load):
        fn, path = load
        self._write(path, {
            "notifications": True,
            "timers": [{"title": "A", "duration": 900}],
        })
        r = fn()
        assert r["timers"][0]["notify"] is True

    def test_old_global_sound_mute_becomes_timer_default_off(self, load):
        # there's no separate global mute anymore — an old install that had
        # everything muted just means every timer defaults to no sound
        fn, path = load
        self._write(path, {"sound": "mute", "timers": [{"duration": 900}, {"duration": 900}]})
        r = fn()
        assert r["timers"][0]["sound"] is None
        assert r["timers"][1]["sound"] is None

    def test_timer_with_own_sound_not_overridden_by_legacy_global(self, load):
        fn, path = load
        self._write(path, {
            "sound": "long",
            "timers": [{"duration": 900, "sound": "short"}],
        })
        r = fn()
        # this timer already has its own value — the legacy global default
        # only fills in for timers that don't have one at all
        assert r["timers"][0]["sound"] == "short"

    def test_no_legacy_fields_defaults_to_short_and_false(self, load):
        fn, path = load
        self._write(path, {"timers": [{"duration": 900}]})
        r = fn()
        assert r["timers"][0]["sound"] == "short"
        assert r["timers"][0]["notify"] is False


class TestLoadSettingsPartialData:
    def _write(self, path, data):
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_only_pinned_present_rest_are_defaults(self, load):
        fn, path = load
        self._write(path, {"pinned": True})
        r = fn()
        assert r["pinned"] is True
        assert r["window_x"] is None
        # No "timers" key and no "titles" key → legacy path fires with
        # titles=None → uses [""] → one timer with empty title, default duration
        assert len(r["timers"]) == 1
        assert r["timers"][0]["title"] == ""
        assert r["timers"][0]["duration"] == 15 * 60
        assert r["timers"][0]["sound"] == "short"
        assert r["timers"][0]["notify"] is False

    def test_empty_json_object_gives_all_defaults(self, load):
        fn, path = load
        self._write(path, {})
        r = fn()
        assert r["pinned"] is False
        # Same legacy path: no "timers" key → one empty-title timer
        assert len(r["timers"]) == 1
        assert r["timers"][0]["title"] == ""


# ---------------------------------------------------------------------------
# parse_input
# ---------------------------------------------------------------------------

class TestParseInputMinutesOnly:
    def test_plain_integer_treated_as_minutes(self, parse):
        assert parse("5") == 5 * 60

    def test_plain_integer_with_m_suffix(self, parse):
        assert parse("10m") == 10 * 60

    def test_plain_integer_with_min_suffix(self, parse):
        assert parse("30min") == 30 * 60

    def test_plain_integer_with_minutes_suffix(self, parse):
        assert parse("1minutes") == 60

    def test_zero_minutes_returns_none(self, parse):
        assert parse("0") is None

    def test_zero_m_returns_none(self, parse):
        assert parse("0m") is None


class TestParseInputSeconds:
    def test_seconds_suffix(self, parse):
        assert parse("90s") == 90

    def test_1_second(self, parse):
        assert parse("1s") == 1

    def test_0_seconds_returns_none(self, parse):
        assert parse("0s") is None

    def test_seconds_old_cap_no_longer_a_limit(self, parse):
        assert parse("359999s") == 359999

    def test_seconds_max_boundary(self, parse):
        assert parse(f"{32 * 24 * 3600}s") == 32 * 24 * 3600

    def test_seconds_over_max_returns_none(self, parse):
        assert parse(f"{32 * 24 * 3600 + 1}s") is None


class TestParseInputHours:
    def test_hours_suffix(self, parse):
        assert parse("2h") == 2 * 3600

    def test_1_hour(self, parse):
        assert parse("1h") == 3600

    def test_0_hours_returns_none(self, parse):
        assert parse("0h") is None

    def test_168_hours_is_7_days(self, parse):
        # the hero-banner "Claude weekly reset" case — 7 days expressed as hours
        assert parse("168h") == 7 * 86400


class TestParseInputDays:
    def test_days_suffix(self, parse):
        assert parse("7d") == 7 * 86400

    def test_1_day(self, parse):
        assert parse("1d") == 86400

    def test_0_days_returns_none(self, parse):
        assert parse("0d") is None

    def test_case_insensitive_days(self, parse):
        assert parse("7D") == 7 * 86400

    def test_days_equals_equivalent_hours(self, parse):
        assert parse("7d") == parse("168h")

    def test_32_days_is_max_boundary(self, parse):
        assert parse("32d") == 32 * 86400

    def test_33_days_over_max_returns_none(self, parse):
        assert parse("33d") is None


class TestParseInputDayHourColonFormat:
    """The "Nd H:MM:SS" shape fmt() itself now produces for durations past
    a day — parse_input needs to round-trip it when a user clicks to edit
    a countdown that's displaying in this format."""

    def test_basic_day_and_time(self, parse):
        assert parse("1d 3:45:12") == 86400 + 3 * 3600 + 45 * 60 + 12

    def test_matches_fmt_output_exactly(self, parse, fmt_fn):
        assert parse(fmt_fn(7 * 86400 + 5 * 3600 + 30 * 60 + 15)) == 7 * 86400 + 5 * 3600 + 30 * 60 + 15

    def test_zero_hour_component(self, parse):
        assert parse("1d 0:00:00") == 86400

    def test_two_digit_hour(self, parse):
        assert parse("2d 23:59:59") == 2 * 86400 + 23 * 3600 + 59 * 60 + 59

    def test_case_insensitive(self, parse):
        assert parse("1D 3:45:12") == 86400 + 3 * 3600 + 45 * 60 + 12

    def test_invalid_minutes_returns_none(self, parse):
        assert parse("1d 3:60:00") is None

    def test_invalid_seconds_returns_none(self, parse):
        assert parse("1d 3:00:60") is None

    def test_over_max_duration_returns_none(self, parse):
        assert parse("33d 0:00:00") is None

    def test_missing_space_does_not_match(self, parse):
        # not the format fmt() produces — reasonable to reject rather than
        # silently guess
        assert parse("1d3:45:12") is None

    def test_hours_over_23_still_arithmetically_valid(self, parse):
        # not a shape fmt() would ever produce itself, but there's no real
        # reason to reject a user typing "extra" hours by hand
        assert parse("1d 30:00:00") == 86400 + 30 * 3600


class TestParseInputColonFormat:
    def test_mm_colon_ss(self, parse):
        assert parse("5:30") == 5 * 60 + 30

    def test_mm_colon_00(self, parse):
        assert parse("15:00") == 15 * 60

    def test_hh_colon_mm_colon_ss(self, parse):
        assert parse("1:30:00") == 1 * 3600 + 30 * 60

    def test_hh_colon_mm_colon_ss_all_parts(self, parse):
        assert parse("2:15:45") == 2 * 3600 + 15 * 60 + 45

    def test_zero_colon_01_is_one_second(self, parse):
        assert parse("0:01") == 1

    def test_zero_colon_00_returns_none(self, parse):
        assert parse("0:00") is None

    def test_seconds_over_59_returns_none(self, parse):
        assert parse("1:60") is None

    def test_minutes_over_59_in_hhmmss_returns_none(self, parse):
        assert parse("1:60:00") is None

    def test_seconds_over_59_in_hhmmss_returns_none(self, parse):
        assert parse("1:00:60") is None

    def test_four_part_colon_returns_none(self, parse):
        assert parse("1:2:3:4") is None

    def test_colon_with_letters_returns_none(self, parse):
        assert parse("1:2a") is None

    def test_old_100_hour_cap_no_longer_a_limit(self, parse):
        # previously rejected at the old 99:59:59 cap; now well within range
        assert parse("100:00:00") == 100 * 3600

    def test_max_valid_hhmmss(self, parse):
        # 99:59:59 = 359999 seconds — no longer the ceiling, just a value in range
        assert parse("99:59:59") == 359999

    def test_result_over_new_max_returns_none(self, parse):
        # colon format has no day unit, so express the 32-day cap as hours
        over_max_hours = (32 * 24) + 1
        assert parse(f"{over_max_hours}:00:00") is None


class TestParseInputEdgeCases:
    def test_empty_string_returns_none(self, parse):
        assert parse("") is None

    def test_whitespace_only_returns_none(self, parse):
        assert parse("   ") is None

    def test_leading_trailing_whitespace_stripped(self, parse):
        assert parse("  5  ") == 5 * 60

    def test_letters_only_returns_none(self, parse):
        assert parse("abc") is None

    def test_negative_number_returns_none(self, parse):
        assert parse("-5") is None

    def test_negative_with_suffix_returns_none(self, parse):
        assert parse("-5m") is None

    def test_decimal_returns_none(self, parse):
        assert parse("5.5") is None

    def test_unknown_suffix_returns_none(self, parse):
        assert parse("5x") is None

    def test_5d_is_now_a_valid_day_suffix(self, parse):
        # was rejected pre-day-support; "d" is now a recognized suffix
        assert parse("5d") == 5 * 86400

    def test_large_valid_minutes(self, parse):
        # 5999 minutes = 359940 seconds — was near the old cap, now nowhere close
        assert parse("5999") == 5999 * 60

    def test_old_6000_minute_cap_no_longer_a_limit(self, parse):
        # 6000 * 60 = 360000 — used to exceed the old 359999 cap
        assert parse("6000") == 6000 * 60

    def test_minutes_over_new_max_returns_none(self, parse):
        over_max_minutes = (32 * 24 * 60) + 1
        assert parse(str(over_max_minutes)) is None

    def test_case_insensitive_suffix(self, parse):
        assert parse("5M") == 5 * 60

    def test_case_insensitive_hours(self, parse):
        assert parse("1H") == 3600

    def test_case_insensitive_seconds(self, parse):
        assert parse("30S") == 30


class TestParseInputMixedUnits:
    def test_hours_and_minutes(self, parse):
        assert parse("3h14m") == 3 * 3600 + 14 * 60

    def test_hr_alias_and_minutes(self, parse):
        assert parse("3hr14m") == 3 * 3600 + 14 * 60

    def test_days_hours_minutes(self, parse):
        assert parse("1d2h15m") == 86400 + 2 * 3600 + 15 * 60

    def test_spaces_between_tokens_allowed(self, parse):
        assert parse("1d 2h 15m") == 86400 + 2 * 3600 + 15 * 60

    def test_all_four_units(self, parse):
        assert parse("1d2h3m4s") == 86400 + 2 * 3600 + 3 * 60 + 4

    def test_case_insensitive(self, parse):
        assert parse("3H14M") == 3 * 3600 + 14 * 60

    def test_units_in_any_order(self, parse):
        assert parse("15m3h") == 3 * 3600 + 15 * 60

    def test_repeated_unit_is_rejected(self, parse):
        # ambiguous, not "add them up" — reject rather than guess intent
        assert parse("1h2h") is None

    def test_trailing_garbage_after_valid_tokens_rejected(self, parse):
        assert parse("3h14mx") is None

    def test_leading_garbage_before_valid_tokens_rejected(self, parse):
        assert parse("x3h14m") is None

    def test_mixed_over_max_duration_returns_none(self, parse):
        assert parse("33d") is None

    def test_bare_number_still_means_minutes_not_a_bad_token(self, parse):
        # unchanged legacy behaviour: no unit at all still means minutes
        assert parse("45") == 45 * 60


# ---------------------------------------------------------------------------
# fmt
# ---------------------------------------------------------------------------

class TestFmt:
    def test_less_than_hour_shows_mm_ss(self, fmt_fn):
        assert fmt_fn(90) == "01:30"

    def test_zero_seconds(self, fmt_fn):
        assert fmt_fn(0) == "00:00"

    def test_59_seconds(self, fmt_fn):
        assert fmt_fn(59) == "00:59"

    def test_60_seconds(self, fmt_fn):
        assert fmt_fn(60) == "01:00"

    def test_3599_seconds(self, fmt_fn):
        assert fmt_fn(3599) == "59:59"

    def test_3600_seconds_shows_hh_mm_ss(self, fmt_fn):
        assert fmt_fn(3600) == "1:00:00"

    def test_3661_seconds(self, fmt_fn):
        assert fmt_fn(3661) == "1:01:01"

    def test_23_hours_59_stays_hour_format_below_a_day(self, fmt_fn):
        assert fmt_fn(23 * 3600 + 59 * 60 + 59) == "23:59:59"

    def test_7200_two_hours(self, fmt_fn):
        assert fmt_fn(7200) == "2:00:00"

    def test_padding_single_digit_minutes_and_seconds(self, fmt_fn):
        assert fmt_fn(65) == "01:05"

    def test_exactly_one_day_switches_to_day_format(self, fmt_fn):
        assert fmt_fn(86400) == "1d 0:00:00"

    def test_one_day_one_second_before_stays_hour_format(self, fmt_fn):
        assert fmt_fn(86399) == "23:59:59"

    def test_seven_days(self, fmt_fn):
        assert fmt_fn(7 * 86400) == "7d 0:00:00"

    def test_359999_seconds_is_now_day_format(self, fmt_fn):
        # 359999s = 4 days, 3:59:59 remainder — this used to be the old
        # 99:59:59 cap boundary; now it's comfortably past the 1-day
        # threshold where day-prefixed formatting kicks in
        assert fmt_fn(359999) == "4d 3:59:59"

    def test_day_format_hour_has_no_leading_zero(self, fmt_fn):
        # matches the existing non-day hour format's own convention
        assert fmt_fn(86400 + 3 * 3600) == "1d 3:00:00"

    def test_thirty_two_day_max_boundary(self, fmt_fn):
        assert fmt_fn(32 * 86400 - 1) == "31d 23:59:59"


class TestLoadSettingsStrictBooleans:
    """pinned/muted are honoured only as real JSON booleans; anything else
    (strings, ints, null) falls back to the default False."""

    @pytest.mark.parametrize("key", ["pinned", "muted"])
    @pytest.mark.parametrize("bad", ["false", "true", "yes", 1, 0, 2, None, [], {}])
    def test_non_boolean_value_falls_back_to_false(self, load, key, bad):
        fn, f = load
        f.write_text(json.dumps({key: bad}))
        assert fn()[key] is False

    @pytest.mark.parametrize("key", ["pinned", "muted"])
    def test_real_true_is_honoured(self, load, key):
        fn, f = load
        f.write_text(json.dumps({key: True}))
        assert fn()[key] is True

    @pytest.mark.parametrize("key", ["pinned", "muted"])
    def test_real_false_is_honoured(self, load, key):
        fn, f = load
        f.write_text(json.dumps({key: False}))
        assert fn()[key] is False

    def test_missing_pinned_and_muted_default_to_false(self, load):
        fn, f = load
        f.write_text(json.dumps({}))
        r = fn()
        assert r["pinned"] is False and r["muted"] is False

    def test_bad_pinned_does_not_discard_valid_muted(self, load):
        fn, f = load
        f.write_text(json.dumps({"pinned": "true", "muted": True}))
        r = fn()
        assert r["pinned"] is False and r["muted"] is True


class TestLoadSettingsLegacyNotificationsStrict:
    """Legacy global "notifications" key enables the per-timer notify default
    only when it is the literal JSON true."""

    def _notify_of_first_timer(self, load, value):
        fn, f = load
        f.write_text(json.dumps({"notifications": value, "timers": [{"title": "a"}]}))
        return fn()["timers"][0]["notify"]

    def test_literal_true_enables_legacy_notify_default(self, load):
        assert self._notify_of_first_timer(load, True) is True

    @pytest.mark.parametrize("value", [False, "false", "true", 1, 0, None])
    def test_non_true_value_does_not_enable_legacy_notify(self, load, value):
        assert self._notify_of_first_timer(load, value) is False

    def test_absent_key_leaves_notify_off(self, load):
        fn, f = load
        f.write_text(json.dumps({"timers": [{"title": "a"}]}))
        assert fn()["timers"][0]["notify"] is False

    def test_string_false_does_not_enable_notify_in_legacy_titles_path(self, load):
        fn, f = load
        f.write_text(json.dumps({"notifications": "false", "titles": ["a"]}))
        assert fn()["timers"][0]["notify"] is False

    def test_explicit_per_timer_notify_beats_legacy_key(self, load):
        fn, f = load
        f.write_text(json.dumps({"notifications": True,
                                 "timers": [{"title": "a", "notify": False}]}))
        assert fn()["timers"][0]["notify"] is False


_ISO_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}) (.*)$")


class TestLogErrorFormat:
    def test_line_starts_with_iso_timestamp_then_space_then_message(self, ns):
        namespace, f = ns
        namespace["_log_error"]("boom happened")
        lines = f.with_name("error.log").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        m = _ISO_LINE.match(lines[0])
        assert m, f"bad log line: {lines[0]!r}"
        assert m.group(2) == "boom happened"

    def test_timestamp_is_parseable_and_recent(self, ns):
        from datetime import datetime, timedelta
        namespace, f = ns
        before = datetime.now().replace(microsecond=0)
        namespace["_log_error"]("x")
        line = f.with_name("error.log").read_text(encoding="utf-8").splitlines()[0]
        stamp = datetime.fromisoformat(_ISO_LINE.match(line).group(1))
        assert before <= stamp <= datetime.now() + timedelta(seconds=1)

    def test_multiple_calls_append_in_order(self, ns):
        namespace, f = ns
        for msg in ("first", "second", "third"):
            namespace["_log_error"](msg)
        lines = f.with_name("error.log").read_text(encoding="utf-8").splitlines()
        assert [_ISO_LINE.match(l).group(2) for l in lines] == ["first", "second", "third"]

    def test_empty_message_still_logs_timestamped_line(self, ns):
        namespace, f = ns
        namespace["_log_error"]("")
        line = f.with_name("error.log").read_text(encoding="utf-8").splitlines()[0]
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2} $", line)

    def test_unwritable_log_location_does_not_raise(self, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("i am a file, not a directory")
        namespace = _make_namespace(str(blocker / "settings.json"))
        namespace["_log_error"]("cannot be written")  # must not raise
        assert blocker.read_text() == "i am a file, not a directory"

    def test_log_failure_during_write_does_not_raise(self, ns):
        namespace, f = ns
        with mock.patch("builtins.open", side_effect=PermissionError("denied")):
            namespace["_log_error"]("denied write")  # must not raise
        assert not f.with_name("error.log").exists()


# ---------------------------------------------------------------------------
# parse_input: oversized input (A4) — int() raises ValueError past ~4300
# digits, which used to escape parse_input and leave the UI stuck.
# ---------------------------------------------------------------------------

class TestParseInputOversized:
    @pytest.mark.parametrize("text", [
        "9" * 5000,              # bare minutes branch
        "9" * 5000 + "d 1:00:00",  # day-prefix branch
        "1:" + "9" * 5000,       # colon branch
        "1h" + "9" * 5000 + "m",  # mixed-unit branch
    ])
    def test_thousands_of_digits_returns_none_not_raise(self, parse, text):
        assert parse(text) is None

    def test_over_40_chars_rejected_even_if_otherwise_valid(self, parse):
        assert parse("0" * 41 + "5") is None  # "5" minutes, padded past the cap

    def test_exactly_40_chars_still_parsed(self, parse):
        assert parse("0" * 38 + "10") == 10 * 60

    def test_longest_realistic_inputs_still_parse(self, parse):
        assert parse("29d 23:59:59") == 29 * 86400 + 23 * 3600 + 59 * 60 + 59
        assert parse("1d 2h 15m 30s") == 86400 + 2 * 3600 + 15 * 60 + 30


# ---------------------------------------------------------------------------
# A2: a read failure (lock) must not be treated as a corrupt file, and a
# failed backup must block saving instead of letting the next save overwrite
# the original.
# ---------------------------------------------------------------------------

def _locked_open(settings_file, exc):
    """builtins.open replacement that fails only for the settings file."""
    real_open = open
    def fake(path, *a, **k):
        if str(path) == str(settings_file):
            raise exc
        return real_open(path, *a, **k)
    return fake


class TestLoadSettingsReadFailureKeepsFile:
    def test_locked_file_is_left_in_place_untouched(self, ns):
        namespace, f = ns
        content = json.dumps({"timers": [{"title": "keep me", "duration": 600}]})
        f.write_text(content)
        with mock.patch("builtins.open", _locked_open(f, PermissionError("locked"))):
            result = namespace["_load_settings"]()
        assert result["timers"] == []                         # defaults returned
        assert f.read_text() == content                       # original untouched
        assert not f.with_name("settings.json.bak").exists()  # not moved aside

    def test_locked_file_blocks_saving_and_is_logged(self, ns):
        namespace, f = ns
        f.write_text(json.dumps({"timers": []}))
        with mock.patch("builtins.open", _locked_open(f, PermissionError("locked"))):
            namespace["_load_settings"]()
        assert namespace["_settings_write_blocked"] is True
        assert "could not read settings" in f.with_name("error.log").read_text()

    def test_stat_failure_is_not_mistaken_for_missing_file(self, ns):
        namespace, f = ns
        f.write_text(json.dumps({"timers": []}))
        with mock.patch("os.path.getsize", side_effect=PermissionError("locked")):
            namespace["_load_settings"]()
        assert namespace["_settings_write_blocked"] is True
        assert f.exists()

    def test_missing_file_does_not_block_saving(self, ns):
        namespace, f = ns
        namespace["_load_settings"]()
        assert namespace["_settings_write_blocked"] is False

    def test_corrupt_file_backed_up_does_not_block_saving(self, ns):
        namespace, f = ns
        f.write_text("{not json")
        namespace["_load_settings"]()
        assert f.with_name("settings.json.bak").exists()
        assert namespace["_settings_write_blocked"] is False

    def test_failed_backup_blocks_saving_and_keeps_original(self, ns):
        namespace, f = ns
        f.write_text("{not json")
        with mock.patch("os.replace", side_effect=PermissionError("locked")):
            namespace["_load_settings"]()
        assert namespace["_settings_write_blocked"] is True
        assert f.read_text() == "{not json"                   # original still there
        assert "could not back up" in f.with_name("error.log").read_text()

    def test_backup_helper_returns_true_on_success_false_on_failure(self, ns):
        namespace, f = ns
        f.write_text("x")
        assert namespace["_backup_corrupt_settings"]() is True
        f.write_text("y")
        with mock.patch("os.replace", side_effect=OSError("nope")):
            assert namespace["_backup_corrupt_settings"]() is False
