import customtkinter as ctk
import threading
import sys
import re

# ── Platform sound ─────────────────────────────────────────────────────────────
_WAVS = {}

if sys.platform == "win32":
    from winotify import Notification
    import winsound, struct, math

    def _wrap_wav(raw, rate=44100):
        return struct.pack("<4sI4s4sIHHIIHH4sI",
            b"RIFF", 36 + len(raw), b"WAVE",
            b"fmt ", 16, 1, 1, rate, rate * 2, 2, 16,
            b"data", len(raw)) + raw

    def _sine_segment(freq, duration, tau, volume=0.5, rate=44100, fade_ms=0):
        n = int(rate * duration)
        fade_samples = int(rate * fade_ms / 1000)
        buf = bytearray(n * 2)
        for i in range(n):
            t = i / rate
            fade = (n - i) / fade_samples if fade_samples and i >= n - fade_samples else 1.0
            val = int(volume * math.exp(-t / tau) * 32767 * math.sin(2 * math.pi * freq * t) * fade)
            struct.pack_into("<h", buf, i * 2, max(-32767, min(32767, val)))
        return bytes(buf)

    def _apply_reverb(raw, rate=44100, delay_ms=70, echo_amp=0.30, tail_ms=350):
        n = len(raw) // 2
        tail = int(rate * tail_ms / 1000)
        delay = int(rate * delay_ms / 1000)
        samples = list(struct.unpack(f"<{n}h", raw)) + [0] * tail
        for i in range(delay, len(samples)):
            val = samples[i] + int(samples[i - delay] * echo_amp)
            samples[i] = max(-32767, min(32767, val))
        return struct.pack(f"<{len(samples)}h", *samples)

    def _build_wavs(vol):
        _WAVS["short"]  = _wrap_wav(_apply_reverb(_sine_segment(880, 0.4, 0.18, volume=vol)))
        _WAVS["medium"] = _wrap_wav(_apply_reverb(_sine_segment(587, 0.15, 0.12, volume=vol) + _sine_segment(880, 0.35, 0.18, volume=vol)))
        _WAVS["long"]   = _wrap_wav(_apply_reverb(_sine_segment(587, 0.15, 0.12, volume=vol) + _sine_segment(880, 0.15, 0.12, volume=vol) + _sine_segment(1175, 0.25, 0.18, volume=vol, fade_ms=10)))

    _build_wavs(0.25)  # 50% of 0.5 max

    def _play(wav):
        winsound.PlaySound(wav, winsound.SND_MEMORY)
else:
    def _build_wavs(vol):
        pass
    def _play(wav):
        print("\a", end="", flush=True)

_vol_pct = 50  # integer 0–100 in 5% steps; audio volume = _vol_pct / 100 * 0.5
_sound_mode = "short"
_last_sound = "short"
_notify_enabled = False

def beep():
    if _sound_mode == "mute":
        return
    threading.Thread(target=_play, args=(_WAVS.get(_sound_mode),), daemon=True).start()

def notify(title, duration):
    if not _notify_enabled or sys.platform != "win32":
        return
    msg = f"Your timer '{title}' has finished." if title else f"Your {duration} timer has finished."
    def _send():
        toast = Notification(app_id="About Time", title="Timer finished", msg=msg)
        toast.show()
    threading.Thread(target=_send, daemon=True).start()

# ── Helpers ────────────────────────────────────────────────────────────────────
def fmt(seconds):
    if seconds >= 3600:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h}:{m:02d}:{s:02d}"
    m = seconds // 60
    s = seconds % 60
    return f"{m:02d}:{s:02d}"


def parse_input(text):
    text = text.strip()
    if not text:
        return None
    if ":" in text:
        parts = text.split(":")
        try:
            nums = [int(p) for p in parts]
        except ValueError:
            return None
        if len(parts) == 2:
            m, s = nums
            if not (0 <= s <= 59):
                return None
            total = m * 60 + s
        elif len(parts) == 3:
            h, m, s = nums
            if not (0 <= m <= 59 and 0 <= s <= 59):
                return None
            total = h * 3600 + m * 60 + s
        else:
            return None
        return total if 1 <= total <= 359999 else None
    match = re.match(r"^(\d+)\s*([a-z]*)$", text.lower())
    if not match:
        return None
    value = int(match.group(1))
    unit = match.group(2)
    if not unit or unit.startswith("m"):
        total = value * 60
    elif unit.startswith("s"):
        total = value
    elif unit.startswith("h"):
        total = value * 3600
    else:
        return None
    return total if 1 <= total <= 359999 else None


_FLASH_COLORS = [f"#{int(255*(1-i/19)+26*(i/19)):02X}0000" for i in range(20)]
BTN_W = 72

def _make_tip():
    return ctk.CTkLabel(
        root, text="",
        fg_color=("#4a4a4a", "#2a2a2a"),
        corner_radius=4,
        font=ctk.CTkFont(size=16),
    )


# ── Timer widget ───────────────────────────────────────────────────────────────
class TimerWidget(ctk.CTkFrame):
    def __init__(self, parent, deletable=False, on_delete=None, initial_title="", **kwargs):
        super().__init__(parent, fg_color="transparent", border_width=0, corner_radius=8, **kwargs)
        self.duration_seconds = 15 * 60
        self.remaining_seconds = 15 * 60
        self.state = "idle"
        self.after_id = None
        self.last_valid_display = "15:00"
        self.editing_countdown = False
        self.display_var = ctk.StringVar(value="15:00")
        self.edit_var = ctk.StringVar()
        self._build(deletable, on_delete, initial_title)

    def _build(self, deletable, on_delete, initial_title=""):
        if deletable:
            del_btn = ctk.CTkButton(
                self, text="✕", width=26, height=26,
                font=ctk.CTkFont(size=14),
                fg_color="transparent",
                hover_color=("#5a2a2a", "#5a2a2a"),
                command=on_delete,
            )
            del_btn.place(x=4, y=4)

            del_tip = ctk.CTkLabel(
                self, text="Remove this timer",
                fg_color=("#4a4a4a", "#2a2a2a"),
                corner_radius=4,
                font=ctk.CTkFont(size=16),
            )
            del_btn.bind("<Enter>", lambda e: (del_tip.place(x=34, y=7), del_tip.lift()))
            del_btn.bind("<Leave>", lambda e: del_tip.place_forget())

        self.title_entry = ctk.CTkEntry(
            self,
            placeholder_text="Click to enter title",
            border_width=0,
            fg_color="transparent",
            font=ctk.CTkFont(size=16),
            justify="center",
        )
        self.title_entry.pack(fill="x", padx=(36, 36), pady=(6, 0))
        self.title_entry.bind("<Return>", lambda e: self.focus())
        if initial_title:
            self.title_entry.insert(0, initial_title)

        self.countdown_frame = ctk.CTkFrame(self, fg_color="transparent", border_width=0)
        self.countdown_frame.pack(pady=(2, 2))

        self.countdown_label = ctk.CTkLabel(
            self.countdown_frame,
            textvariable=self.display_var,
            font=ctk.CTkFont(size=28, weight="bold"),
            cursor="hand2",
        )
        self.countdown_label.pack(padx=8, pady=1)
        self.countdown_label.bind("<Button-1>", self._on_countdown_click)

        self.countdown_entry = ctk.CTkEntry(
            self.countdown_frame,
            textvariable=self.edit_var,
            font=ctk.CTkFont(size=28, weight="bold"),
            justify="center",
            width=160,
            border_width=1,
        )
        self.countdown_entry.bind("<Return>", self._on_entry_return)
        self.countdown_entry.bind("<FocusOut>", self._commit_countdown)

        self.btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.btn_frame.pack(pady=(2, 6))

        _ibtn = {"font": ctk.CTkFont(size=16), "width": BTN_W}
        self.start_btn   = ctk.CTkButton(self.btn_frame, text="▶", command=self._do_start,   **_ibtn)
        self.restart_btn = ctk.CTkButton(self.btn_frame, text="↺", command=self._do_restart, **_ibtn)
        self.pause_btn   = ctk.CTkButton(self.btn_frame, text="⏸", command=self._do_stop,    **_ibtn)
        self.stop_btn    = ctk.CTkButton(self.btn_frame, text="⏹", command=self._do_stop,    **_ibtn)
        self.resume_btn  = ctk.CTkButton(self.btn_frame, text="▶", command=self._do_resume,  **_ibtn)

        self._set_state("idle")

    # ── Flash ──────────────────────────────────────────────────────────────────
    def flash_invalid(self, step=0):
        if step < len(_FLASH_COLORS):
            self.configure(border_width=2, border_color=_FLASH_COLORS[step])
            self.after(100, lambda: self.flash_invalid(step + 1))
        else:
            self.configure(border_width=0)

    # ── Tick ───────────────────────────────────────────────────────────────────
    def _tick(self):
        if self.state != "running":
            self.after_id = None
            return
        self.remaining_seconds -= 1
        if self.remaining_seconds <= 0:
            self.remaining_seconds = 0
            self.after_id = None
            self.display_var.set("Done!")
            self._set_state("finished")
            beep()
            notify(self.title_entry.get().strip(), self.last_valid_display)
        else:
            self.display_var.set(fmt(self.remaining_seconds))
            self.after_id = root.after(1000, self._tick)

    # ── Actions ────────────────────────────────────────────────────────────────
    def _start_running(self):
        self.remaining_seconds = self.duration_seconds
        self.display_var.set(fmt(self.remaining_seconds))
        self._set_state("running")
        self.after_id = root.after(1000, self._tick)

    def _do_start(self):
        if parse_input(self.display_var.get()) is None:
            self.flash_invalid()
            return
        self._start_running()

    def _do_stop(self):
        if self.after_id:
            root.after_cancel(self.after_id)
            self.after_id = None
        if self.state == "running":
            self._set_state("paused")
        elif self.state == "paused":
            self.remaining_seconds = self.duration_seconds
            self.display_var.set(self.last_valid_display)
            self._set_state("idle")

    def _do_resume(self):
        self._set_state("running")
        self.after_id = root.after(1000, self._tick)

    def _do_restart(self):
        if self.after_id:
            root.after_cancel(self.after_id)
            self.after_id = None
        self._start_running()

    # ── State machine ──────────────────────────────────────────────────────────
    def _set_state(self, new_state):
        self.state = new_state
        for btn in (self.start_btn, self.restart_btn, self.stop_btn, self.resume_btn, self.pause_btn):
            btn.pack_forget()
        if new_state == "idle":
            self.start_btn.pack(padx=4, pady=2)
        elif new_state == "running":
            self.restart_btn.configure(width=BTN_W)
            self.restart_btn.pack(side="left", padx=4, pady=2)
            self.pause_btn.pack(side="left", padx=4, pady=2)
        elif new_state == "paused":
            btn_w = max(40, (root.winfo_width() - 70) // 3)
            for btn in (self.stop_btn, self.resume_btn, self.restart_btn):
                btn.configure(width=btn_w)
                btn.pack(side="left", padx=2, pady=2)
        elif new_state == "finished":
            self.restart_btn.configure(width=BTN_W)
            self.restart_btn.pack(padx=4, pady=2)

    # ── Countdown click-to-edit ────────────────────────────────────────────────
    def _on_countdown_click(self, event=None):
        if self.editing_countdown:
            return
        self.editing_countdown = True
        self.edit_var.set(self.last_valid_display if self.state == "finished" else self.display_var.get())
        self.countdown_label.pack_forget()
        self.countdown_entry.pack(padx=10, pady=5)
        self.countdown_entry.focus()
        self.countdown_entry.after(10, lambda: self.countdown_entry.select_range(0, "end"))

    def _commit_countdown(self, event=None):
        if not self.editing_countdown:
            return
        self.editing_countdown = False
        text = self.edit_var.get()
        seconds = parse_input(text)
        self.countdown_entry.pack_forget()
        self.countdown_label.pack(padx=10, pady=5)
        if seconds is None:
            if self.state == "running":
                self.display_var.set(fmt(self.remaining_seconds))
            elif self.state == "finished":
                self.display_var.set("Done!")
            else:
                self.display_var.set(self.last_valid_display)
            return
        self.duration_seconds = seconds
        self.remaining_seconds = seconds
        self.last_valid_display = fmt(seconds)
        self.display_var.set(self.last_valid_display)
        if self.state in ("idle", "running", "paused"):
            if self.after_id:
                root.after_cancel(self.after_id)
                self.after_id = None
            self._set_state("running")
            self.after_id = root.after(1000, self._tick)

    def _on_entry_return(self, event=None):
        self._commit_countdown()
        return "break"


# ── Root window ────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
root = ctk.CTk()
root.title("About Time")
root.resizable(True, True)
root.minsize(250, 130)

# ── Timers ─────────────────────────────────────────────────────────────────────
MAX_TIMERS = 5

timers_frame = ctk.CTkFrame(root, fg_color="transparent")
timers_frame.pack(fill="x")

# Each entry is (separator_or_None, TimerWidget)
timers = []
_snap_heights = {}     # timer_count -> measured height (kept for calibration)
_snap_unit = None      # height increment per timer, derived from first two measurements
_snap_btn_offset = None  # button frame height removed at MAX_TIMERS, derived from h[5] measurement
_resize_pending = False

def _snap_h(n):
    """Formula-based snap height for n timers; falls back to measured if uncalibrated."""
    base = _snap_heights.get(1)
    if base and _snap_unit:
        h = base + (n - 1) * _snap_unit
        if n == MAX_TIMERS and _snap_btn_offset is not None:
            h -= _snap_btn_offset
        return h
    return _snap_heights.get(n)

def add_timer(deletable=False, initial_title=""):
    if len(timers) >= MAX_TIMERS:
        return
    sep = None
    if timers:
        sep = ctk.CTkFrame(timers_frame, height=1, fg_color=("#444444", "#333333"))
        sep.pack(fill="x", padx=12, pady=2)
    tw = TimerWidget(timers_frame, deletable=deletable, on_delete=lambda: remove_timer(tw),
                     initial_title=initial_title)
    tw.pack(fill="x")
    timers.append((sep, tw))
    _update_add_btn()   # hide button first so height is measured without it at cap
    _fit_window()

def remove_timer(tw):
    if tw.after_id:
        root.after_cancel(tw.after_id)
    for i, (sep, t) in enumerate(timers):
        if t is tw:
            if sep:
                sep.pack_forget()
                sep.destroy()
            tw.pack_forget()
            tw.destroy()
            timers.pop(i)
            break
    _update_add_btn()   # restore button before measuring height
    _fit_window()

def _fit_window():
    global _snap_unit, _snap_btn_offset
    root.update_idletasks()
    h = root.winfo_reqheight()
    n = len(timers)
    _snap_heights[n] = h

    if _snap_unit is None and 1 in _snap_heights and 2 in _snap_heights:
        _snap_unit = _snap_heights[2] - _snap_heights[1]

    if (_snap_btn_offset is None and _snap_unit is not None
            and 1 in _snap_heights and MAX_TIMERS in _snap_heights):
        raw = _snap_heights[1] + (MAX_TIMERS - 1) * _snap_unit
        _snap_btn_offset = raw - _snap_heights[MAX_TIMERS]

    target = _snap_h(n) or h
    root.minsize(250, _snap_h(1) or 130)
    root.maxsize(9999, _snap_h(MAX_TIMERS) or target)
    root.geometry(f"{root.winfo_width()}x{target}")

# ── Height-snap on resize ──────────────────────────────────────────────────────
def _snap_candidates():
    return [h for h in (_snap_h(i) for i in range(1, len(timers) + 1)) if h]

def _on_resize(event):
    global _resize_pending
    if event.widget is not root or _resize_pending or not _snap_heights:
        return
    candidates = _snap_candidates()
    if not candidates:
        return
    target = min(candidates, key=lambda h: abs(h - event.height))
    if event.height != target:
        _resize_pending = True
        root.after_idle(_do_snap)

def _do_snap():
    global _resize_pending
    _resize_pending = False
    candidates = _snap_candidates()
    if not candidates:
        return
    current_h = root.winfo_height()
    target = min(candidates, key=lambda h: abs(h - current_h))
    if current_h != target:
        root.geometry(f"{root.winfo_width()}x{target}")

# ── Add timer button ───────────────────────────────────────────────────────────
add_btn_frame = ctk.CTkFrame(root, fg_color="transparent")
add_btn_frame.pack(fill="x", pady=(0, 6))

add_btn = ctk.CTkButton(
    add_btn_frame,
    text="+ Add timer",
    width=120,
    font=ctk.CTkFont(size=16),
    command=lambda: add_timer(deletable=True),
)
add_btn.pack()

_CLOCK_SYMS = {1: "◓", 2: "◑", 3: "◒", 4: "◐"}

def _update_add_btn():
    if len(timers) >= MAX_TIMERS:
        add_btn_frame.pack_forget()
    else:
        add_btn.configure(text=f"{_CLOCK_SYMS.get(len(timers), '◷')} Add timer")
        add_btn_frame.pack(fill="x", pady=(0, 6))

# ── Pin / Always on top ────────────────────────────────────────────────────────
# Created after all pack() widgets so place() renders on top of them.
topmost_var = ctk.BooleanVar(value=False)

def toggle_topmost():
    topmost_var.set(not topmost_var.get())
    root.wm_attributes("-topmost", topmost_var.get())
    _update_pin()
    if _tip.winfo_ismapped():
        _show_tip()

def _update_pin():
    pin_btn.configure(fg_color=("#1F6AA5", "#1F6AA5") if topmost_var.get() else "transparent")

_tip = _make_tip()

def _show_tip(event=None):
    _tip.configure(text="Always on top: On" if topmost_var.get() else "Always on top: Off")
    _tip.place(x=34, y=7)

def _hide_tip(event=None):
    _tip.place_forget()

pin_btn = ctk.CTkButton(
    root, text="↑", width=26, height=26,
    font=ctk.CTkFont(size=14),
    fg_color="transparent",
    hover_color=("#3a3a4a", "#3a3a4a"),
    command=toggle_topmost,
)
pin_btn.place(x=4, y=4)
pin_btn.bind("<Enter>", _show_tip)
pin_btn.bind("<Leave>", _hide_tip)
_update_pin()

# ── Notification toggle ────────────────────────────────────────────────────────
def toggle_notify():
    global _notify_enabled
    _notify_enabled = not _notify_enabled
    _update_notify_btn()
    if _notify_tip.winfo_ismapped():
        _show_notify_tip()

def _update_notify_btn():
    notify_btn.configure(fg_color=("#1F6AA5", "#1F6AA5") if _notify_enabled else "transparent")

_notify_tip = _make_tip()

def _show_notify_tip(event=None):
    _notify_tip.configure(text="Notifications: On" if _notify_enabled else "Notifications: Off")
    _notify_tip.place(x=34, y=35)

def _hide_notify_tip(event=None):
    _notify_tip.place_forget()

notify_btn = ctk.CTkButton(
    root, text="!", width=26, height=26,
    font=ctk.CTkFont(size=14),
    fg_color="transparent",
    hover_color=("#3a3a4a", "#3a3a4a"),
    command=toggle_notify,
)
notify_btn.place(x=4, y=32)
notify_btn.bind("<Enter>", _show_notify_tip)
notify_btn.bind("<Leave>", _hide_notify_tip)

# ── Volume control ─────────────────────────────────────────────────────────────
_vol_tip = _make_tip()

def _show_vol_tip(event=None):
    _vol_tip.configure(text=f"Volume: {_vol_pct}%")
    _vol_tip.place(x=34, y=63)

def _hide_vol_tip(event=None):
    _vol_tip.place_forget()

def _change_volume(delta):
    global _vol_pct
    was_zero = _vol_pct == 0
    _vol_pct = min(100, max(0, _vol_pct + delta))
    _build_wavs(_vol_pct / 200)
    if _vol_pct == 0:
        _set_sound("mute", preview=False)
    elif was_zero:
        _set_sound(_last_sound, preview=False)
    _show_vol_tip()

vol_up_btn = ctk.CTkButton(
    root, text="▲", width=26, height=26,
    font=ctk.CTkFont(size=11),
    fg_color="transparent",
    hover_color=("#3a3a4a", "#3a3a4a"),
    command=lambda: _change_volume(5),
)
vol_up_btn.place(x=4, y=60)
vol_up_btn.bind("<Enter>", _show_vol_tip)
vol_up_btn.bind("<Leave>", _hide_vol_tip)

vol_dn_btn = ctk.CTkButton(
    root, text="▼", width=26, height=26,
    font=ctk.CTkFont(size=11),
    fg_color="transparent",
    hover_color=("#3a3a4a", "#3a3a4a"),
    command=lambda: _change_volume(-5),
)
vol_dn_btn.place(x=4, y=88)
vol_dn_btn.bind("<Enter>", _show_vol_tip)
vol_dn_btn.bind("<Leave>", _hide_vol_tip)

# ── Sound selector ─────────────────────────────────────────────────────────────
_sound_btns = {}
_sound_labels = {
    "short":  "Single chime",
    "medium": "Rising chime",
    "long":   "Task complete",
    "mute":   "Silence alarms",
}
_sound_tip_y = {"short": 7, "medium": 35, "long": 63, "mute": 91}

def _set_sound(mode, preview=True):
    global _sound_mode, _last_sound
    if mode != "mute":
        _last_sound = mode
    _sound_mode = mode
    for m, btn in _sound_btns.items():
        btn.configure(fg_color=("#1F6AA5", "#1F6AA5") if m == mode else "transparent")
    if preview:
        beep()

_sound_tip = _make_tip()

def _show_sound_tip(mode, event=None):
    _sound_tip.configure(text=_sound_labels[mode])
    _sound_tip.place(relx=1.0, anchor="ne", x=-30, y=_sound_tip_y[mode])
    _sound_tip.lift()

def _hide_sound_tip(event=None):
    _sound_tip.place_forget()

sound_frame = ctk.CTkFrame(root, fg_color="transparent")
sound_frame.place(relx=1.0, anchor="ne", x=-2, y=2)

for _sym, _mode, _row in [
    ("♪", "short",  0),
    ("♫", "medium", 1),
    ("♬", "long",   2),
    ("⊘", "mute",   3),
]:
    _btn = ctk.CTkButton(
        sound_frame, text=_sym,
        width=26, height=26,
        font=ctk.CTkFont(size=14),
        fg_color="transparent",
        hover_color=("#3a3a4a", "#3a3a4a"),
        command=lambda m=_mode: _set_sound(m),
    )
    _btn.grid(row=_row, column=0, padx=1, pady=1)
    _btn.bind("<Enter>", lambda e, m=_mode: _show_sound_tip(m))
    _btn.bind("<Leave>", _hide_sound_tip)
    _sound_btns[_mode] = _btn

_sound_btns["short"].configure(fg_color=("#1F6AA5", "#1F6AA5"))

# ── Init ───────────────────────────────────────────────────────────────────────
add_timer(deletable=False, initial_title="About Time")
root.bind("<Configure>", _on_resize)

root.mainloop()
