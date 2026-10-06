"""
Tooltips in the real, fully built app window (both layouts).

Runs the real script in a subprocess (own temporary APPDATA, mainloop replaced
by a scripted dump) and drives genuine <Enter>/<Leave> events on the real
widgets. Covers: the volume tip shows the live percentage; the three sound
icons and the bell have tips; the add-timer tile has one in BOTH stack and row
layouts and the tip stays inside the window; the transport buttons stay quiet
on a casual mouse-over and only show after the long hold; every tip stays
inside its box and is gone after the pointer leaves.
"""

import json
import os
import site
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SRC_PATH = Path(__file__).parent.parent / "about_time.py"

_DUMP = '''
import time
def pump(ms):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        root.update(); time.sleep(0.01)
def placed_tips(owner):
    return [c for c in owner.winfo_children()
            if isinstance(c, ctk.CTkLabel) and c.winfo_manager() == "place" and c.cget("text")]
def box(w, owner):
    return [w.winfo_rootx()-owner.winfo_rootx(), w.winfo_rooty()-owner.winfo_rooty(), w.winfo_width(), w.winfo_height()]
def fire(widget, seq):
    target = getattr(widget, "_canvas", widget)
    target.event_generate(seq, x=3, y=3)
def hover(widget, owner, wait):
    fire(widget, "<Enter>"); pump(wait)
    tips = placed_tips(owner)
    info = {"texts": [t.cget("text") for t in tips],
            "boxes": [box(t, owner) for t in tips],
            "owner": [owner.winfo_width(), owner.winfo_height()],
            "target": box(widget, owner)}
    fire(widget, "<Leave>"); pump(150)
    info["after_leave"] = len(placed_tips(owner))
    return info

root.update_idletasks(); root.update()
add_timer(deletable=True)
root.update_idletasks(); root.update()
tw = timers[0][1]
out = {"hold": TIP_HOLD_MS, "sound": {}, "bell": None, "controls": {}, "add": None}

for k, b in tw._sound_btns.items():
    out["sound"][k] = hover(b, tw, 200)
tw.sound_mode = "medium"
out["sound_on"] = hover(tw._sound_btns["medium"], tw, 200)
globals()["_muted"] = True
out["sound_muted"] = hover(tw._sound_btns["short"], tw, 200)
globals()["_muted"] = False
out["bell"] = hover(tw.notify_btn, tw, 200)
tw.notify_enabled = True
out["bell_on"] = hover(tw.notify_btn, tw, 200)

# casual mouse-over of a transport button: nothing; after the hold: a tip
out["start_casual"] = hover(tw.start_btn, tw, 600)
fire(tw.start_btn, "<Enter>"); pump(TIP_HOLD_MS + 400)
out["start_held"] = {"texts": [t.cget("text") for t in placed_tips(tw)], "boxes": [box(t, tw) for t in placed_tips(tw)],
                     "owner": [tw.winfo_width(), tw.winfo_height()]}
fire(tw.start_btn, "<Leave>"); pump(150)
out["start_held"]["after_leave"] = len(placed_tips(tw))

# a click while a tip is pending/visible clears it
fire(tw.restart_btn, "<Enter>"); fire(tw.restart_btn, "<Button-1>"); pump(TIP_HOLD_MS + 400)
out["press_clears_pending"] = len(placed_tips(tw))

# add tile: real tile, current layout
out["add"] = hover(_add_tile, root, 200)

# clicking the add tile rebuilds it: its tip must go with it, not linger
fire(_add_tile, "<Enter>"); pump(200)
out["add_tip_before_click"] = len(placed_tips(root))
fire(_add_tile, "<Button-1>"); pump(400)
out["add_tip_after_click"] = len(placed_tips(root))
out["timers_after_click"] = len(timers)
fire(_add_tile, "<Enter>"); pump(200)
out["add_tip_on_rebuilt_tile"] = [t.cget("text") for t in placed_tips(root)]
fire(_add_tile, "<Leave>"); pump(150)
out["add_tip_after_leave_rebuilt"] = len(placed_tips(root))

# volume tip with a stubbed mixer level
globals()["_get_app_volume"] = lambda: 0.63
out["volume_text"] = _volume_tip_text()
globals()["_muted"] = True
out["volume_text_muted"] = _volume_tip_text()
globals()["_muted"] = False
globals()["_get_app_volume"] = lambda: None
out["volume_text_none"] = _volume_tip_text()
print("TIPS " + json.dumps(out))
import sys; sys.stdout.flush(); os._exit(0)
'''

_RUNNER = '''
import json, os, sys
src = open(sys.argv[1], encoding="utf-8").read()
src = src.replace("root.mainloop()", sys.argv[2])
exec(compile(src, sys.argv[1], "exec"), {"__name__": "__main__", "__file__": sys.argv[1], "json": json})
'''


def _run(mode):
    appdata = tempfile.mkdtemp(prefix="at_tips_")
    os.makedirs(os.path.join(appdata, "About Time"))
    with open(os.path.join(appdata, "About Time", "settings.json"), "w") as f:
        json.dump({"layout_mode": mode}, f)
    env = dict(os.environ, APPDATA=appdata, PYTHONUSERBASE=site.getuserbase())
    proc = subprocess.run([sys.executable, "-c", _RUNNER, str(SRC_PATH), _DUMP],
                          capture_output=True, text=True, timeout=180, env=env)
    for line in proc.stdout.splitlines():
        if line.startswith("TIPS "):
            return json.loads(line[len("TIPS "):])
    pytest.fail(f"no tip data from the real app ({mode}): {proc.stdout[-300:]} {proc.stderr[-800:]}")


def _inside(box, owner):
    x, y, w, h = box
    return x >= 0 and y >= 0 and x + w <= owner[0] and y + h <= owner[1]


def _overlap(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


@pytest.fixture(scope="module", params=["stack", "row"])
def tips(request):
    if sys.platform != "win32":
        pytest.skip("needs the Windows build of the app")
    return _run(request.param)


def test_sound_icons_each_have_a_tip_inside_the_box(tips):
    expect = {"short": "Short chime", "medium": "Medium chime", "long": "Long chime"}
    for mode, name in expect.items():
        info = tips["sound"][mode]
        assert len(info["texts"]) == 1 and info["texts"][0].startswith(name), info
        assert _inside(info["boxes"][0], info["owner"]), info
        assert not _overlap(info["boxes"][0], info["target"]), info
        assert info["after_leave"] == 0


def test_sound_tip_states_active_and_muted(tips):
    assert tips["sound_on"]["texts"] == ["Medium chime: On\nClick to turn off"]
    assert tips["sound_muted"]["texts"] == ["Short chime\nLocked while muted"]


def test_bell_has_a_tip_that_follows_its_state(tips):
    assert tips["bell"]["texts"] == ["Notification: Off"]
    assert tips["bell_on"]["texts"] == ["Notification: On"]
    assert _inside(tips["bell"]["boxes"][0], tips["bell"]["owner"])


def test_transport_buttons_stay_quiet_on_a_casual_mouse_over(tips):
    assert tips["hold"] == 2000
    assert tips["start_casual"]["texts"] == []


def test_transport_button_tip_appears_after_the_long_hold_and_clears(tips):
    held = tips["start_held"]
    assert held["texts"] == ["Start"]
    assert _inside(held["boxes"][0], held["owner"])
    assert held["after_leave"] == 0


def test_pressing_a_button_cancels_its_pending_tip(tips):
    assert tips["press_clears_pending"] == 0


def test_add_timer_tile_has_a_tip_in_this_layout_inside_the_window(tips):
    info = tips["add"]
    assert info["texts"] == ["Add a timer"], info
    assert _inside(info["boxes"][0], info["owner"]), info
    assert not _overlap(info["boxes"][0], info["target"]), info
    assert info["after_leave"] == 0


def test_volume_tip_shows_the_live_percentage(tips):
    assert tips["volume_text"] == "Volume: 63%\nRight-click to mute"
    assert tips["volume_text_muted"] == "Volume: 63% (Muted)\nRight-click to unmute"
    assert tips["volume_text_none"] == "Volume\nRight-click to mute"


def test_add_tile_tip_goes_away_when_the_click_rebuilds_the_tile(tips):
    assert tips["add_tip_before_click"] == 1
    assert tips["timers_after_click"] == 3
    assert tips["add_tip_after_click"] == 0


def test_rebuilt_add_tile_still_shows_and_clears_its_tip(tips):
    assert tips["add_tip_on_rebuilt_tile"] == ["Add a timer"]
    assert tips["add_tip_after_leave_rebuilt"] == 0
