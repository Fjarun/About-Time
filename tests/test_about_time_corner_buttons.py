"""
Corner-column placement in the real, fully built app window.

Volume and mute are one speaker button in the bottom-left corner slot. Layout
tests elsewhere stub the corner buttons out, so this runs the real script (own
temporary APPDATA, mainloop replaced by a geometry dump, then exit) in a
subprocess for each layout mode and checks that no corner button overlaps timer
content or another button, and that the speaker's slider popup and tooltip stay
inside the window and clear of the title text.
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
root.update_idletasks(); root.update()
for _ in range(4):
    add_timer()
root.update_idletasks(); root.update()
def box(w):
    return [w.winfo_rootx()-root.winfo_rootx(), w.winfo_rooty()-root.winfo_rooty(), w.winfo_width(), w.winfo_height()]
out = {"root": [root.winfo_width(), root.winfo_height()], "corner": {}, "content": [], "popup": None, "tips": {}}
for n in ("pin_btn", "layout_btn", "mute_btn"):
    out["corner"][n] = box(globals()[n])
out["has_volume_btn"] = "volume_btn" in globals()
for i, (_sep, tw) in enumerate(timers):
    out["content"].append(["title%d" % i, box(tw.title_entry)])
    out["content"].append(["countdown%d" % i, box(tw.countdown_frame)])
    out["content"].append(["buttons%d" % i, box(tw.btn_frame)])
    for k, b in tw._sound_btns.items():
        out["content"].append(["sound%d-%s" % (i, k), box(b)])
_show_volume_popup(); root.update_idletasks(); root.update()
out["popup"] = box(volume_popup)
_hide_volume_popup()
for state in (False, True):
    globals()["_muted"] = state
    _show_mute_tip(); root.update_idletasks(); root.update()
    out["tips"]["muted" if state else "unmuted"] = box(_mute_tip)
    _mute_tip.place_forget()
globals()["_muted"] = False
_update_mute_btn(); root.update()
clicks = []
for _ in range(2):
    mute_btn._canvas.event_generate("<Button-3>", x=5, y=5); root.update()
    clicks.append([_muted, mute_btn.cget("text"), [t.sound_mode is not None and _muted for _s, t in timers][0],
                   all(b.cget("state") == "disabled" for _s, t in timers for b in t._sound_btns.values())])
out["rightclicks"] = clicks
mute_btn.invoke(); root.update()
out["popup_after_click"] = _vol_popup_open
print("GEOMETRY " + json.dumps(out))
import sys; sys.stdout.flush(); os._exit(0)
'''

_RUNNER = '''
import json, os, sys
src = open(sys.argv[1], encoding="utf-8").read()
src = src.replace("root.mainloop()", sys.argv[2])
exec(compile(src, sys.argv[1], "exec"), {"__name__": "__main__", "__file__": sys.argv[1], "json": json})
'''


def _run(mode):
    with tempfile.TemporaryDirectory(prefix="at_corner_", ignore_cleanup_errors=True) as appdata:
        os.makedirs(os.path.join(appdata, "About Time"))
        with open(os.path.join(appdata, "About Time", "settings.json"), "w") as f:
            json.dump({"layout_mode": mode}, f)
        # APPDATA also locates the user-level Python packages, so keep those findable.
        env = dict(os.environ, APPDATA=appdata, PYTHONUSERBASE=site.getuserbase())
        proc = subprocess.run([sys.executable, "-c", _RUNNER, str(SRC_PATH), _DUMP],
                              capture_output=True, text=True, timeout=120, env=env)
    for line in proc.stdout.splitlines():
        if line.startswith("GEOMETRY "):
            return json.loads(line[len("GEOMETRY "):])
    pytest.fail(f"no geometry from the real app ({mode}): {proc.stdout[-300:]} {proc.stderr[-600:]}")


def _overlap(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def _inside(box, root):
    x, y, w, h = box
    return x >= 0 and y >= 0 and x + w <= root[0] and y + h <= root[1]


@pytest.fixture(scope="module", params=["stack", "row"])
def geo(request):
    if sys.platform != "win32":
        pytest.skip("needs the Windows build of the app")
    return _run(request.param)


def test_volume_and_mute_are_one_button(geo):
    assert geo["has_volume_btn"] is False


def test_no_corner_button_overlaps_title_text(geo):
    for name, box in geo["corner"].items():
        hits = [n for n, b in geo["content"] if n.startswith("title") and _overlap(box, b)]
        assert not hits, (name, hits)


def test_speaker_button_covers_no_timer_content(geo):
    hits = [n for n, b in geo["content"] if _overlap(geo["corner"]["mute_btn"], b)]
    assert not hits, hits


def test_corner_buttons_do_not_overlap_each_other(geo):
    items = list(geo["corner"].items())
    for i, (na, a) in enumerate(items):
        for nb, b in items[i + 1:]:
            assert not _overlap(a, b), (na, nb)


def test_volume_popup_stays_inside_window_and_clear_of_titles(geo):
    assert _inside(geo["popup"], geo["root"]), geo["popup"]
    hits = [n for n, b in geo["content"] if n.startswith("title") and _overlap(geo["popup"], b)]
    assert not hits, hits


@pytest.mark.parametrize("state", ["unmuted", "muted"])
def test_two_line_tooltip_stays_inside_window(geo, state):
    assert _inside(geo["tips"][state], geo["root"]), geo["tips"][state]


def test_right_click_mutes_and_unmutes_and_greys_the_sound_icons(geo):
    first, second = geo["rightclicks"]
    assert first[0] is True and first[1] == "🔇" and first[3] is True
    assert second[0] is False and second[1] == "🔊" and second[3] is False


def test_left_click_opens_the_slider_popup(geo):
    assert geo["popup_after_click"] is True
