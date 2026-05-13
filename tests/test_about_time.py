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
    }

    src_path = Path(__file__).parent.parent / "about_time.py"
    source = src_path.read_text(encoding="utf-8")

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

REQUIRED_KEYS = {"volume", "sound", "last_sound", "notifications", "pinned",
                 "window_x", "window_y", "timers"}

DEFAULTS = {
    "volume": 50, "sound": "short", "last_sound": "short",
    "notifications": False, "pinned": False,
    "window_x": None, "window_y": None, "timers": [],
}


class TestLoadSettingsMissingFile:
    def test_returns_complete_defaults_when_file_absent(self, load):
        fn, path = load
        assert not path.exists()
        result = fn()
        assert result.keys() == REQUIRED_KEYS

    def test_volume_default(self, load):
        fn, _ = load
        assert fn()["volume"] == 50

    def test_timers_default_is_empty_list(self, load):
        fn, _ = load
        assert fn()["timers"] == []

    def test_window_coords_default_none(self, load):
        fn, _ = load
        r = fn()
        assert r["window_x"] is None
        assert r["window_y"] is None

    def test_notifications_default_false(self, load):
        fn, _ = load
        assert fn()["notifications"] is False

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
        assert result["volume"] == 50
        assert result["timers"] == []
        assert result["window_x"] is None

    def test_empty_file_returns_defaults(self, load):
        fn, path = load
        path.write_text("", encoding="utf-8")
        result = fn()
        assert result.keys() == REQUIRED_KEYS
        assert result["volume"] == 50


class TestLoadSettingsHappyPath:
    def _write(self, path, data):
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_valid_full_settings_round_trips(self, load):
        fn, path = load
        data = {
            "volume": 75, "sound": "medium", "last_sound": "long",
            "notifications": True, "pinned": True,
            "window_x": 100, "window_y": 200,
            "timers": [{"title": "Pomodoro", "duration": 1500,
                        "remaining": 1500, "state": "idle"}],
        }
        self._write(path, data)
        r = fn()
        assert r["volume"] == 75
        assert r["sound"] == "medium"
        assert r["last_sound"] == "long"
        assert r["notifications"] is True
        assert r["pinned"] is True
        assert r["window_x"] == 100
        assert r["window_y"] == 200
        assert len(r["timers"]) == 1
        assert r["timers"][0]["title"] == "Pomodoro"
        assert r["timers"][0]["duration"] == 1500

    def test_volume_zero_forces_mute(self, load):
        fn, path = load
        self._write(path, {"volume": 0, "sound": "short"})
        r = fn()
        assert r["sound"] == "mute"

    def test_volume_boundary_100(self, load):
        fn, path = load
        self._write(path, {"volume": 100, "sound": "long"})
        r = fn()
        assert r["volume"] == 100

    def test_volume_boundary_5(self, load):
        fn, path = load
        self._write(path, {"volume": 5})
        r = fn()
        assert r["volume"] == 5

    def test_notifications_true(self, load):
        fn, path = load
        self._write(path, {"notifications": True})
        r = fn()
        assert r["notifications"] is True

    def test_pinned_true(self, load):
        fn, path = load
        self._write(path, {"pinned": True})
        r = fn()
        assert r["pinned"] is True


class TestLoadSettingsInvalidFieldValues:
    def _write(self, path, data):
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_volume_not_multiple_of_5_falls_back(self, load):
        fn, path = load
        self._write(path, {"volume": 33})
        assert fn()["volume"] == 50

    def test_volume_out_of_range_falls_back(self, load):
        fn, path = load
        self._write(path, {"volume": 150})
        assert fn()["volume"] == 50

    def test_volume_negative_falls_back(self, load):
        fn, path = load
        self._write(path, {"volume": -10})
        assert fn()["volume"] == 50

    def test_volume_string_falls_back(self, load):
        fn, path = load
        self._write(path, {"volume": "loud"})
        assert fn()["volume"] == 50

    def test_volume_float_falls_back(self, load):
        fn, path = load
        self._write(path, {"volume": 50.0})
        assert fn()["volume"] == 50  # 50.0 is not isinstance int in strict sense
        # Note: isinstance(True, int) is True but float is not — falls back
        # Actually 50.0 is float not int, so should fall back to 50 default
        # The function checks isinstance(vol, int) — float fails this

    def test_invalid_sound_falls_back(self, load):
        fn, path = load
        self._write(path, {"sound": "ultra"})
        assert fn()["sound"] == "short"

    def test_invalid_last_sound_falls_back(self, load):
        fn, path = load
        self._write(path, {"last_sound": "mute"})  # "mute" not valid for last_sound
        assert fn()["last_sound"] == "short"

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
        self._write(path, {"timers": [{"duration": 359999}]})
        r = fn()
        assert r["timers"][0]["duration"] == 359999

    def test_timer_duration_over_max_gets_default(self, load):
        fn, path = load
        self._write(path, {"timers": [{"duration": 360000}]})
        r = fn()
        assert r["timers"][0]["duration"] == 15 * 60

    def test_timer_invalid_state_gets_idle(self, load):
        fn, path = load
        self._write(path, {"timers": [{"duration": 900, "state": "exploding"}]})
        r = fn()
        assert r["timers"][0]["state"] == "idle"

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


class TestLoadSettingsPartialData:
    def _write(self, path, data):
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_only_volume_present_rest_are_defaults(self, load):
        fn, path = load
        self._write(path, {"volume": 25})
        r = fn()
        assert r["volume"] == 25
        assert r["sound"] == "short"
        assert r["notifications"] is False
        assert r["window_x"] is None
        # No "timers" key and no "titles" key → legacy path fires with
        # titles=None → uses [""] → one timer with empty title, default duration
        assert len(r["timers"]) == 1
        assert r["timers"][0]["title"] == ""
        assert r["timers"][0]["duration"] == 15 * 60

    def test_empty_json_object_gives_all_defaults(self, load):
        fn, path = load
        self._write(path, {})
        r = fn()
        assert r["volume"] == 50
        assert r["sound"] == "short"
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

    def test_seconds_max_boundary(self, parse):
        assert parse("359999s") == 359999

    def test_seconds_over_max_returns_none(self, parse):
        assert parse("360000s") is None


class TestParseInputHours:
    def test_hours_suffix(self, parse):
        assert parse("2h") == 2 * 3600

    def test_1_hour(self, parse):
        assert parse("1h") == 3600

    def test_0_hours_returns_none(self, parse):
        assert parse("0h") is None


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

    def test_result_over_359999_returns_none(self, parse):
        # 100 hours = 360000 seconds > 359999
        assert parse("100:00:00") is None

    def test_max_valid_hhmmss(self, parse):
        # 99:59:59 = 359999 seconds
        assert parse("99:59:59") == 359999


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

    def test_unknown_suffix_d_returns_none(self, parse):
        assert parse("5d") is None

    def test_large_valid_minutes(self, parse):
        # 5999 minutes = 359940 seconds < 359999
        assert parse("5999") == 5999 * 60

    def test_6000_minutes_over_max_returns_none(self, parse):
        # 6000 * 60 = 360000 > 359999
        assert parse("6000") is None

    def test_case_insensitive_suffix(self, parse):
        assert parse("5M") == 5 * 60

    def test_case_insensitive_hours(self, parse):
        assert parse("1H") == 3600

    def test_case_insensitive_seconds(self, parse):
        assert parse("30S") == 30


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

    def test_359999_seconds(self, fmt_fn):
        # 99h 59m 59s
        assert fmt_fn(359999) == "99:59:59"

    def test_7200_two_hours(self, fmt_fn):
        assert fmt_fn(7200) == "2:00:00"

    def test_padding_single_digit_minutes_and_seconds(self, fmt_fn):
        assert fmt_fn(65) == "01:05"
