"""
Merged volume/mute speaker button: the slider, mute and unmute logic.

The real Windows audio session can't be exercised headless, so the volume block of
about_time.py is exec'd with stubbed mixer calls and a recording _set_muted. What
this proves is the logic (which slider values mute, when volume is restored), not
actual sound output.
"""

import re
import types
from pathlib import Path

import pytest

SRC = (Path(__file__).parent.parent / "about_time.py").read_text(encoding="utf-8")


def _chunk():
    m = re.search(r"(^_ZERO_PCT = \d+.*?)(?=^volume_popup = ctk\.CTkFrame)", SRC, re.DOTALL | re.MULTILINE)
    assert m, "volume block not found in about_time.py"
    return m.group(1)


@pytest.fixture()
def ns():
    calls = types.SimpleNamespace(volume=[], muted=[], slider=[], hidden_tip=[], after=[], cancelled=[])
    state = {"mixer": 0.8}

    def get_volume():
        return state["mixer"]

    def set_volume(level):
        calls.volume.append(level)
        state["mixer"] = level

    env = {
        "_muted": False,
        "_get_app_volume": get_volume,
        "_set_app_volume": set_volume,
        "volume_slider": types.SimpleNamespace(set=lambda v: calls.slider.append(v)),
        "_mute_tip": types.SimpleNamespace(place_forget=lambda: calls.hidden_tip.append(1)),
        "root": types.SimpleNamespace(
            after=lambda ms, fn: (calls.after.append((ms, fn)), len(calls.after))[1],
            after_cancel=lambda job: calls.cancelled.append(job)),
        "volume_popup": types.SimpleNamespace(place=lambda **k: None, lift=lambda: None,
                                              place_forget=lambda: None),
    }

    def set_muted(value):
        calls.muted.append(value)
        env["_muted"] = value

    env["_set_muted"] = set_muted
    exec(_chunk(), env)
    env["calls"] = calls
    env["state"] = state
    return env


class TestSliderMutes:
    def test_slider_to_zero_mutes(self, ns):
        ns["_on_volume_slider_change"](0)
        assert ns["calls"].muted == [True]
        assert ns["calls"].volume == [0.0]

    def test_only_an_exact_zero_mutes(self, ns):
        assert ns["_ZERO_PCT"] == 0
        ns["_on_volume_slider_change"](0.0)
        assert ns["calls"].muted == [True]

    @pytest.mark.parametrize("low", [1, 3])
    def test_one_two_three_percent_do_not_mute(self, ns, low):
        ns["_on_volume_slider_change"](low)
        assert ns["calls"].muted == []
        assert ns["calls"].volume == [low / 100.0]

    def test_slider_just_above_threshold_does_not_mute(self, ns):
        ns["_on_volume_slider_change"](ns["_ZERO_PCT"] + 1)
        assert ns["calls"].muted == []

    def test_slider_up_while_muted_unmutes(self, ns):
        ns["_muted"] = True
        ns["_on_volume_slider_change"](40)
        assert ns["calls"].muted == [False]
        assert ns["calls"].volume == [0.4]

    def test_dragging_while_already_in_the_right_state_never_flips(self, ns):
        for v in (10, 30, 60, 90):
            ns["_on_volume_slider_change"](v)
        assert ns["calls"].muted == []
        ns["_muted"] = True
        ns["_on_volume_slider_change"](0)
        ns["_on_volume_slider_change"](0)
        assert ns["calls"].muted == []


class TestUnmuteRestoresAudibleVolume:
    def test_volume_at_zero_is_restored_to_half(self, ns):
        ns["state"]["mixer"] = 0.0
        ns["_restore_volume_if_silent"]()
        assert ns["calls"].volume == [0.5]

    def test_one_percent_counts_as_audible_and_is_left_alone(self, ns):
        ns["state"]["mixer"] = 0.01
        ns["_restore_volume_if_silent"]()
        assert ns["calls"].volume == []

    def test_audible_volume_is_left_alone(self, ns):
        ns["state"]["mixer"] = 0.7
        ns["_restore_volume_if_silent"]()
        assert ns["calls"].volume == []

    def test_unknown_volume_is_left_alone(self, ns):
        ns["_get_app_volume"] = lambda: None
        ns["_restore_volume_if_silent"]()
        assert ns["calls"].volume == []

    def test_open_slider_follows_the_restore(self, ns):
        ns["state"]["mixer"] = 0.0
        ns["_vol_popup_open"] = True
        ns["_restore_volume_if_silent"]()
        assert ns["calls"].slider == [50]

    def test_closed_slider_is_not_touched(self, ns):
        ns["state"]["mixer"] = 0.0
        ns["_restore_volume_if_silent"]()
        assert ns["calls"].slider == []


class TestPopup:
    def test_opening_the_popup_hides_the_tooltip(self, ns):
        ns["_show_volume_popup"]()
        assert ns["calls"].hidden_tip == [1]
        assert ns["_vol_popup_open"] is True
        assert set(ns["calls"].slider) == {80.0}   # set on open, then again by the first sync

    def test_toggle_closes_an_open_popup(self, ns):
        ns["_show_volume_popup"]()
        ns["_toggle_volume_popup"]()
        assert ns["_vol_popup_open"] is False


class TestFollowsWindowsMixer:
    """An outside volume change (Windows' own mixer) is followed, edge-triggered."""

    def _seen(self, ns, level):
        """Record `level` as the last volume the app saw."""
        ns["state"]["mixer"] = level
        ns["_sync_with_mixer"]()
        ns["calls"].muted.clear()
        ns["calls"].slider.clear()

    def test_first_sighting_changes_nothing(self, ns):
        ns["state"]["mixer"] = 0.0
        ns["_sync_with_mixer"]()
        assert ns["calls"].muted == []

    def test_windows_dragged_to_zero_mutes(self, ns):
        self._seen(ns, 0.8)
        ns["state"]["mixer"] = 0.0
        ns["_sync_with_mixer"]()
        assert ns["calls"].muted == [True]

    @pytest.mark.parametrize("low", [0.01, 0.02])
    def test_windows_dragged_to_one_or_two_percent_does_not_mute(self, ns, low):
        self._seen(ns, 0.8)
        ns["state"]["mixer"] = low
        ns["_sync_with_mixer"]()
        assert ns["calls"].muted == []

    def test_windows_raised_from_one_percent_while_muted_by_zero_unmutes(self, ns):
        self._seen(ns, 0.0)
        ns["_muted"] = True
        ns["state"]["mixer"] = 0.01
        ns["_sync_with_mixer"]()
        assert ns["calls"].muted == [False]

    def test_windows_raised_from_zero_unmutes_without_a_restore(self, ns):
        self._seen(ns, 0.0)
        ns["_muted"] = True
        ns["state"]["mixer"] = 0.43
        ns["_sync_with_mixer"]()
        assert ns["calls"].muted == [False]
        assert ns["calls"].volume == []

    def test_a_right_click_mute_at_audible_volume_is_never_undone(self, ns):
        self._seen(ns, 0.8)
        ns["_muted"] = True          # muted by right-click, volume untouched
        for _ in range(5):
            ns["_sync_with_mixer"]()
        assert ns["calls"].muted == []

    def test_tiny_float_noise_is_not_a_change(self, ns):
        self._seen(ns, 0.4)
        ns["_muted"] = True
        ns["state"]["mixer"] = 0.4004
        ns["_sync_with_mixer"]()
        assert ns["calls"].muted == []

    def test_the_apps_own_slider_change_is_not_seen_as_outside(self, ns):
        """Drag to 50%, then right-click mute: the next sync must not read the
        app's own 50% as an outside change and undo the mute."""
        self._seen(ns, 0.8)
        ns["_on_volume_slider_change"](50)
        ns["_muted"] = True
        ns["calls"].muted.clear()
        ns["_sync_with_mixer"]()
        assert ns["calls"].muted == []

    def test_mute_by_slider_then_windows_raises_then_reopen_unmutes(self, ns):
        """The exact sequence from testing: slider to 0, Windows up to 43%, reopen."""
        self._seen(ns, 0.2)
        ns["_on_volume_slider_change"](0)
        assert ns["_muted"] is True
        ns["state"]["mixer"] = 0.43
        ns["_show_volume_popup"]()
        assert ns["_muted"] is False

    def test_unknown_volume_changes_nothing(self, ns):
        self._seen(ns, 0.5)
        ns["_get_app_volume"] = lambda: None
        ns["_sync_with_mixer"]()
        assert ns["calls"].muted == []

    def test_open_slider_follows_windows(self, ns):
        self._seen(ns, 0.5)
        ns["_vol_popup_open"] = True
        ns["state"]["mixer"] = 0.7
        ns["_sync_with_mixer"]()
        assert ns["calls"].slider[-1] == pytest.approx(70)

    def test_closed_slider_is_left_alone(self, ns):
        self._seen(ns, 0.5)
        ns["state"]["mixer"] = 0.7
        ns["_sync_with_mixer"]()
        assert ns["calls"].slider == []


class TestPolling:
    def test_opening_starts_a_half_second_poll(self, ns):
        ns["_show_volume_popup"]()
        assert [ms for ms, _ in ns["calls"].after] == [500]

    def test_poll_reschedules_while_open_and_stops_when_closed(self, ns):
        ns["_show_volume_popup"]()
        ns["_poll_mixer"]()
        assert len(ns["calls"].after) == 2
        ns["_vol_popup_open"] = False
        ns["_poll_mixer"]()
        assert len(ns["calls"].after) == 2
        assert ns["_poll_job"] is None

    def test_closing_cancels_the_pending_poll(self, ns):
        ns["_show_volume_popup"]()
        ns["_hide_volume_popup"]()
        assert ns["calls"].cancelled == [1]
        assert ns["_poll_job"] is None

    def test_reopening_after_close_has_exactly_one_poll_pending(self, ns):
        ns["_show_volume_popup"]()
        ns["_hide_volume_popup"]()
        ns["_show_volume_popup"]()
        pending = len(ns["calls"].after) - len(ns["calls"].cancelled)
        assert pending == 1


class TestWiring:
    """Source-level guards: these lines connect the pieces the tests above stub."""



    def test_slider_callback_is_connected(self):
        assert "command=_on_volume_slider_change" in SRC

    def test_tooltip_has_the_right_click_second_line(self):
        assert "Right-click to mute" in SRC and "Right-click to unmute" in SRC

    def test_window_focus_re_reads_the_mixer(self):
        assert 'root.bind("<FocusIn>", lambda e: _sync_with_mixer() if e.widget is root else None' in SRC

    def test_windows_own_mute_switch_is_never_touched(self):
        assert "GetMute" not in SRC and "SetMute" not in SRC

    def test_outside_click_handler_spares_the_speaker_button(self):
        assert "if w is mute_btn or w is volume_popup:" in SRC
