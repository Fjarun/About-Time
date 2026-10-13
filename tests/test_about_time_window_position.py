"""
The saved window position used to be accepted if it was anywhere within
+-32000, so an unplugged monitor, a hand-edited value or a position saved while
minimised could leave the always-on-top window somewhere nobody can reach.

_place_on_screen is a pure function over monitor rectangles so every layout
(monitors left of the primary, gaps, taskbar) is tested without hardware.
_monitor_rects is the thin Windows wrapper, checked against the real machine.
"""

import re
import sys
from pathlib import Path

import pytest

SOURCE = (Path(__file__).parent.parent / "about_time.py").read_text(encoding="utf-8")


def _load():
    match = re.search(
        r"(^_TITLE_BAR_H.*?)(?=^# Restore window position before loading timers)",
        SOURCE, re.DOTALL | re.MULTILINE,
    )
    assert match, "window position helpers not found in source"
    logged = []
    ns = {"__builtins__": __builtins__, "sys": sys, "_log_error": logged.append}
    exec(match.group(1), ns)
    ns["_logged"] = logged
    return ns


@pytest.fixture()
def pos():
    return _load()


# (monitor rect, work-area rect), each (left, top, right, bottom)
PRIMARY = ((0, 0, 1920, 1080), (0, 0, 1920, 1040))           # 40 px taskbar at the bottom
LEFT = ((-1920, 0, 0, 1080), (-1920, 0, 0, 1080))             # a second monitor to the left
BELOW_RIGHT = ((1920, 1080, 3840, 2160), (1920, 1080, 3840, 2160))  # diagonal: leaves dead gaps


class TestVisiblePositionsAreUntouched:
    def test_normal_position_on_the_primary(self, pos):
        assert pos["_place_on_screen"](100, 100, [PRIMARY]) == (100, 100)

    def test_position_on_a_monitor_left_of_the_primary(self, pos):
        assert pos["_place_on_screen"](-1500, 200, [PRIMARY, LEFT]) == (-1500, 200)

    def test_window_hanging_part_way_off_an_edge_is_left_alone(self, pos):
        # 60 px of the title bar is still on screen: grab-able, so the user's
        # deliberate placement is respected.
        assert pos["_place_on_screen"](1860, 300, [PRIMARY]) == (1860, 300)

    def test_title_bar_partly_above_the_top_edge_but_still_grabbable(self, pos):
        # 21 of the 31 px of title bar showing (needs 18)
        assert pos["_place_on_screen"](400, -10, [PRIMARY]) == (400, -10)

    def test_position_on_the_second_monitor_of_a_diagonal_layout(self, pos):
        assert pos["_place_on_screen"](2500, 1500, [PRIMARY, BELOW_RIGHT]) == (2500, 1500)


class TestUnreachablePositionsAreMovedBackOnScreen:
    def test_far_off_the_desktop_lands_inside_the_work_area(self, pos):
        x, y = pos["_place_on_screen"](30000, -30000, [PRIMARY])
        assert 0 <= x <= 1920 - pos["_ROOM_W"]
        assert 0 <= y <= 1040 - pos["_ROOM_H"]

    def test_title_bar_dragged_off_the_top_is_brought_back(self, pos):
        # y=-29 used to pass (client area visible) with a 2 px sliver of the
        # title bar, which is the only part you can drag by.
        x, y = pos["_place_on_screen"](400, -29, [PRIMARY])
        assert (x, y) == (400, 0)

    def test_title_bar_hidden_by_the_bottom_edge_is_brought_back(self, pos):
        # y=1030: only 10 px of the bar above the 1040 work-area line
        x, y = pos["_place_on_screen"](400, 1030, [PRIMARY])
        assert (x, y) == (400, 1040 - pos["_ROOM_H"])

    def test_the_rule_is_a_fraction_of_the_bar_so_it_scales_with_display_scaling(self, pos):
        # y=-16: at 150% (46 px bar) 30 px show (needs 27); at 100% (31 px)
        # only 15 show (needs 18).
        assert pos["_place_on_screen"](400, -16, [PRIMARY], 46) == (400, -16)
        assert pos["_place_on_screen"](400, -16, [PRIMARY], 31) == (400, 0)

    def test_only_a_sliver_showing_counts_as_unreachable(self, pos):
        # 20 px of the title bar on screen is less than the 40 px needed
        x, y = pos["_place_on_screen"](1900, 300, [PRIMARY])
        assert x == 1920 - pos["_ROOM_W"] and y == 300

    def test_dead_gap_between_diagonal_monitors_is_not_visible(self, pos):
        # inside the bounding box of both monitors, but on neither
        x, y = pos["_place_on_screen"](2500, 300, [PRIMARY, BELOW_RIGHT])
        monitors = [PRIMARY, BELOW_RIGHT]
        on_one = any(
            m[0][0] <= x and x + pos["_ROOM_W"] <= m[0][2] and
            m[0][1] <= y and y + pos["_ROOM_H"] <= m[0][3]
            for m in monitors)
        assert on_one

    def test_taskbar_is_not_a_place_to_hide(self, pos):
        # y=1070 is on the monitor but under the 1040 px work-area line
        x, y = pos["_place_on_screen"](400, 1070, [PRIMARY])
        assert (x, y) == (400, 1040 - pos["_ROOM_H"])


class TestWhenMonitorsCannotBeQueried:
    def test_no_monitor_list_leaves_the_position_alone(self, pos):
        assert pos["_place_on_screen"](30000, -30000, []) == (30000, -30000)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows display API")
class TestRealMonitorEnumeration:
    def test_this_machine_reports_at_least_one_sane_monitor(self, pos):
        rects = pos["_monitor_rects"]()
        assert rects, pos["_logged"]
        for mon, work in rects:
            assert mon[2] > mon[0] and mon[3] > mon[1]
            assert work[0] >= mon[0] and work[2] <= mon[2]      # work area sits inside
            assert work[1] >= mon[1] and work[3] <= mon[3]

    def test_a_far_off_position_is_pulled_onto_a_real_monitor(self, pos):
        rects = pos["_monitor_rects"]()
        x, y = pos["_place_on_screen"](30000, -30000, rects)
        assert any(m[0][0] <= x < m[0][2] and m[0][1] <= y < m[0][3] for m in rects)
