"""
A day-long countdown ("31d 23:58:47") in the real, fully built app window.

In the first timer's box the corner buttons sit at the left edge; the widest
countdown used to run underneath them in sideways mode. Runs the real script in
a subprocess per layout and checks that the countdown text clears every corner
button and stays inside its own box, while an ordinary countdown keeps the
normal font size.
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
def box(w):
    return [w.winfo_rootx()-root.winfo_rootx(), w.winfo_rooty()-root.winfo_rooty(), w.winfo_width(), w.winfo_height()]
root.update_idletasks(); root.update()
tw = timers[0][1]
out = {"corner": {n: box(globals()[n]) for n in ("pin_btn", "layout_btn", "mute_btn")}}
tw.display_var.set("15:00"); root.update_idletasks(); root.update()
out["normal_size"] = tw.countdown_label.cget("font").cget("size")
out["normal_days_const"] = [COUNTDOWN_PT, COUNTDOWN_DAYS_PT]
tw.display_var.set("31d 23:58:47"); root.update_idletasks(); root.update()
out["days_size"] = tw.countdown_label.cget("font").cget("size")
# the visible text, not the label's padded frame
from tkinter import font as tkfont
f = tkfont.Font(font=tw.countdown_label.cget("font"))
text_w = f.measure("31d 23:58:47")
lb = box(tw.countdown_label)
cx = lb[0] + lb[2] // 2
out["text"] = [cx - text_w // 2, lb[1], text_w, lb[3]]
out["timer"] = box(tw)
tw.display_var.set("15:00"); root.update_idletasks(); root.update()
out["back_to_normal"] = tw.countdown_label.cget("font").cget("size")
print("DAYS " + json.dumps(out))
import sys; sys.stdout.flush(); os._exit(0)
'''

_RUNNER = '''
import json, os, sys
src = open(sys.argv[1], encoding="utf-8").read()
src = src.replace("root.mainloop()", sys.argv[2])
exec(compile(src, sys.argv[1], "exec"), {"__name__": "__main__", "__file__": sys.argv[1], "json": json})
'''


def _run(mode):
    appdata = tempfile.mkdtemp(prefix="at_days_")
    os.makedirs(os.path.join(appdata, "About Time"))
    with open(os.path.join(appdata, "About Time", "settings.json"), "w") as f:
        json.dump({"layout_mode": mode}, f)
    env = dict(os.environ, APPDATA=appdata, PYTHONUSERBASE=site.getuserbase())
    proc = subprocess.run([sys.executable, "-c", _RUNNER, str(SRC_PATH), _DUMP],
                          capture_output=True, text=True, timeout=120, env=env)
    for line in proc.stdout.splitlines():
        if line.startswith("DAYS "):
            return json.loads(line[len("DAYS "):])
    pytest.fail(f"no data from the real app ({mode}): {proc.stdout[-300:]} {proc.stderr[-800:]}")


def _overlap(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


@pytest.fixture(scope="module", params=["stack", "row"])
def data(request):
    if sys.platform != "win32":
        pytest.skip("needs the Windows build of the app")
    return _run(request.param)


def test_day_countdown_clears_every_corner_button(data):
    for name, b in data["corner"].items():
        assert not _overlap(data["text"], b), (name, b, data["text"])


def test_day_countdown_stays_inside_its_box(data):
    t, box = data["text"], data["timer"]
    assert t[0] >= 0 and t[0] + t[2] <= box[2], (t, box)


def test_font_shrinks_only_while_the_text_has_days(data):
    normal, days = data["normal_days_const"]
    assert data["normal_size"] == normal
    assert data["days_size"] == days < normal
    assert data["back_to_normal"] == normal
