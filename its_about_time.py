import customtkinter as ctk
import threading
import sys
import re

# ── Platform sound ─────────────────────────────────────────────────────────────
if sys.platform == "win32":
    import winsound, struct, math
    def _make_wav(freq=880, duration=0.4, volume=0.5, rate=44100):
        n = int(rate * duration)
        samples = b"".join(
            struct.pack("<h", int(volume * 32767 * math.sin(2 * math.pi * freq * i / rate)))
            for i in range(n)
        )
        header = struct.pack("<4sI4s4sIHHIIHH4sI",
            b"RIFF", 36 + len(samples), b"WAVE",
            b"fmt ", 16, 1, 1, rate, rate * 2, 2, 16,
            b"data", len(samples))
        return header + samples
    _BEEP_WAV = _make_wav()
    def _beep():
        winsound.PlaySound(_BEEP_WAV, winsound.SND_MEMORY)
else:
    def _beep():
        print("\a", end="", flush=True)

def beep():
    threading.Thread(target=_beep, daemon=True).start()

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
                font=ctk.CTkFont(size=20),
            )
            del_btn.bind("<Enter>", lambda e: (del_tip.place(x=34, y=7), del_tip.lift()))
            del_btn.bind("<Leave>", lambda e: del_tip.place_forget())

        self.title_entry = ctk.CTkEntry(
            self,
            placeholder_text="Click to enter title",
            border_width=0,
            fg_color="transparent",
            font=ctk.CTkFont(size=14),
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

        self.start_btn   = ctk.CTkButton(self.btn_frame, text="▶  Start",   width=BTN_W, command=self._do_start)
        self.restart_btn = ctk.CTkButton(self.btn_frame, text="↺  Restart", width=BTN_W, command=self._do_restart)
        self.pause_btn   = ctk.CTkButton(self.btn_frame, text="⏸  Pause",   width=BTN_W, command=self._do_stop)
        self.stop_btn    = ctk.CTkButton(self.btn_frame, text="⏹  Stop",    width=BTN_W, command=self._do_stop)
        self.resume_btn  = ctk.CTkButton(self.btn_frame, text="▶  Resume",  width=BTN_W, command=self._do_resume)

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
        else:
            self.display_var.set(fmt(self.remaining_seconds))
            self.after_id = root.after(1000, self._tick)

    # ── Actions ────────────────────────────────────────────────────────────────
    def _do_start(self):
        if parse_input(self.display_var.get()) is None:
            self.flash_invalid()
            return
        self.remaining_seconds = self.duration_seconds
        self.display_var.set(fmt(self.remaining_seconds))
        self._set_state("running")
        self.after_id = root.after(1000, self._tick)

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
        self.remaining_seconds = self.duration_seconds
        if self.state == "finished":
            self.display_var.set(self.last_valid_display)
            self._set_state("idle")
        else:
            self.display_var.set(fmt(self.remaining_seconds))
            self._set_state("running")
            self.after_id = root.after(1000, self._tick)

    # ── State machine ──────────────────────────────────────────────────────────
    def _set_state(self, new_state):
        self.state = new_state
        for btn in (self.start_btn, self.restart_btn, self.stop_btn, self.resume_btn, self.pause_btn):
            btn.pack_forget()
        if new_state == "idle":
            self.start_btn.pack(padx=4, pady=2)
        elif new_state == "running":
            self.restart_btn.pack(side="left", padx=4, pady=2)
            self.pause_btn.pack(side="left", padx=4, pady=2)
        elif new_state == "paused":
            self.resume_btn.pack(side="left", padx=4, pady=2)
            self.stop_btn.pack(side="left", padx=4, pady=2)
            self.restart_btn.pack(side="left", padx=4, pady=2)
        elif new_state == "finished":
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
        if self.state in ("running", "paused"):
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
root.title("It's About Time")
root.resizable(True, True)
root.minsize(250, 130)

# ── Timers ─────────────────────────────────────────────────────────────────────
MAX_TIMERS = 5

timers_frame = ctk.CTkFrame(root, fg_color="transparent")
timers_frame.pack(fill="x")

# Each entry is (separator_or_None, TimerWidget)
timers = []
_snap_heights = {}   # timer_count -> required window height
_resize_pending = False

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
    for k in list(_snap_heights):
        if k > len(timers):
            del _snap_heights[k]
    _update_add_btn()   # restore button before measuring height
    _fit_window()

def _fit_window():
    root.update_idletasks()
    h = root.winfo_reqheight()
    root.geometry(f"{root.winfo_width()}x{h}")
    _snap_heights[len(timers)] = h
    root.minsize(250, _snap_heights.get(1, 130))
    root.maxsize(9999, _snap_heights[len(timers)])

# ── Height-snap on resize ──────────────────────────────────────────────────────
def _on_resize(event):
    global _resize_pending
    if event.widget is not root or _resize_pending or not _snap_heights:
        return
    target = min(_snap_heights.values(), key=lambda h: abs(h - event.height))
    if event.height != target:
        _resize_pending = True
        root.after_idle(_do_snap)

def _do_snap():
    global _resize_pending
    _resize_pending = False
    if not _snap_heights:
        return
    current_h = root.winfo_height()
    target = min(_snap_heights.values(), key=lambda h: abs(h - current_h))
    if current_h != target:
        root.geometry(f"{root.winfo_width()}x{target}")

# ── Add timer button ───────────────────────────────────────────────────────────
add_btn_frame = ctk.CTkFrame(root, fg_color="transparent")
add_btn_frame.pack(fill="x", pady=(0, 6))

add_btn = ctk.CTkButton(
    add_btn_frame,
    text="+ Add timer",
    width=120,
    command=lambda: add_timer(deletable=True),
)
add_btn.pack()

def _update_add_btn():
    if len(timers) >= MAX_TIMERS:
        add_btn_frame.pack_forget()
    else:
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

_tip = ctk.CTkLabel(
    root, text="",
    fg_color=("#4a4a4a", "#2a2a2a"),
    corner_radius=4,
    font=ctk.CTkFont(size=20),
)

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

# ── Init ───────────────────────────────────────────────────────────────────────
add_timer(deletable=False, initial_title="It's About Time")
root.bind("<Configure>", _on_resize)

root.mainloop()
