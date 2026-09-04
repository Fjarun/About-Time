import customtkinter as ctk
import threading
import sys
import re
import json
import os
import base64

__version__ = "0.9.1"

# ── Platform sound ─────────────────────────────────────────────────────────────
_WAVS = {}
_wav_lock = threading.Lock()

if sys.platform == "win32":
    from winotify import Notification
    from winotify import TEMPLATE as _TOAST_TEMPLATE, _run_ps as _toast_run_ps
    from pycaw.pycaw import AudioUtilities
    import winsound, struct, math

    _SAMPLE_RATE = 44100

    def _clamp16(v):
        return max(-32768, min(32767, v))

    def _wrap_wav(raw, rate=_SAMPLE_RATE):
        return struct.pack("<4sI4s4sIHHIIHH4sI",
            b"RIFF", 36 + len(raw), b"WAVE",
            b"fmt ", 16, 1, 1, rate, rate * 2, 2, 16,
            b"data", len(raw)) + raw

    def _sine_segment(freq, duration, tau, volume=0.5, rate=_SAMPLE_RATE, fade_ms=0):
        n = int(rate * duration)
        fade_samples = int(rate * fade_ms / 1000)
        buf = bytearray(n * 2)
        for i in range(n):
            t = i / rate
            fade = (n - i) / fade_samples if fade_samples and i >= n - fade_samples else 1.0
            val = int(volume * math.exp(-t / tau) * 32767 * math.sin(2 * math.pi * freq * t) * fade)
            struct.pack_into("<h", buf, i * 2, _clamp16(val))
        return bytes(buf)

    def _apply_reverb(raw, rate=_SAMPLE_RATE, delay_ms=70, echo_amp=0.30, tail_ms=350):
        n = len(raw) // 2
        tail = int(rate * tail_ms / 1000)
        delay = int(rate * delay_ms / 1000)
        samples = list(struct.unpack(f"<{n}h", raw)) + [0] * tail
        for i in range(delay, len(samples)):
            val = samples[i] + int(samples[i - delay] * echo_amp)
            samples[i] = _clamp16(val)
        return struct.pack(f"<{len(samples)}h", *samples)

    def _build_wavs():
        # Fixed full amplitude, always — loudness is Windows' per-app Volume
        # Mixer's job now, not baked into the samples (see the speaker
        # control below, which reads/writes that same OS-level session).
        short  = _wrap_wav(_apply_reverb(_sine_segment(880, 0.4, 0.18, volume=1.0)))
        medium = _wrap_wav(_apply_reverb(_sine_segment(587, 0.15, 0.12, volume=1.0) + _sine_segment(880, 0.35, 0.18, volume=1.0)))
        long_  = _wrap_wav(_apply_reverb(_sine_segment(587, 0.15, 0.12, volume=1.0) + _sine_segment(880, 0.15, 0.12, volume=1.0) + _sine_segment(1175, 0.25, 0.18, volume=1.0, fade_ms=10)))
        with _wav_lock:
            _WAVS["short"]  = short
            _WAVS["medium"] = medium
            _WAVS["long"]   = long_

    def _play(wav):
        winsound.PlaySound(wav, winsound.SND_MEMORY)

    def _find_own_audio_session():
        """The Windows audio session for this process, if one exists yet —
        None until at least one sound has actually been rendered (a fresh
        launch primes one at boot specifically so this isn't ever the
        reason the volume popup looks broken on first open)."""
        pid = os.getpid()
        try:
            for session in AudioUtilities.GetAllSessions():
                proc = session.Process
                if proc is not None and proc.pid == pid:
                    return session
        except Exception as e:
            print(f"[About Time] _find_own_audio_session failed: {e}", file=sys.stderr)
        return None

    def _get_app_volume():
        session = _find_own_audio_session()
        try:
            if session is None or session.SimpleAudioVolume is None:
                return None
            return session.SimpleAudioVolume.GetMasterVolume()
        except Exception as e:
            # e.g. the audio device was unplugged/switched between finding
            # the session and reading it — same "not available" outcome as
            # session is None above, just discovered a step later.
            print(f"[About Time] _get_app_volume failed: {e}", file=sys.stderr)
            return None

    def _set_app_volume(level):
        session = _find_own_audio_session()
        try:
            if session is None or session.SimpleAudioVolume is None:
                return
            session.SimpleAudioVolume.SetMasterVolume(max(0.0, min(1.0, level)), None)
        except Exception as e:
            print(f"[About Time] _set_app_volume failed: {e}", file=sys.stderr)

    def _prime_audio_session():
        """A real (inaudibly quiet) PlaySound call — Windows only creates a
        per-process audio session once something has actually rendered, so
        without this the volume popup would have nothing to control until
        the first timer finished."""
        silent = _wrap_wav(_apply_reverb(_sine_segment(880, 0.05, 0.05, volume=0.0001)))
        threading.Thread(target=lambda: winsound.PlaySound(silent, winsound.SND_MEMORY), daemon=True).start()
else:
    def _build_wavs():
        pass
    def _play(wav):
        print("\a", end="", flush=True)
    def _get_app_volume():
        return None
    def _set_app_volume(level):
        pass
    def _prime_audio_session():
        pass

# ── Constants ──────────────────────────────────────────────────────────────────
MAX_TIMERS = 5
MAX_DURATION_SECONDS = 30 * 24 * 3600  # 30 days
_TITLE_PLACEHOLDER = "Click to enter title"
_TITLE_PLACEHOLDER_COLOR = "#aaaaaa"
_TITLE_TEXT_COLOR = "#ffffff"

# Fixed per-timer box size — every TimerWidget is forced to this size via
# pack_propagate(False), regardless of state (idle/running/paused/finished
# each naturally want different content width). Sized to comfortably fit the
# countdown at its widest (fmt() has no day unit, so a 30-day duration renders
# as plain hours, e.g. "720:00:00") and the 3-button paused-state row without
# crowding.
TIMER_W = 240
TIMER_H = 150

# ── Settings persistence ────────────────────────────────────────────────────────
def _resolve_settings_path():
    appdata = os.getenv("APPDATA", "")
    if appdata and os.path.isabs(appdata) and os.path.isdir(appdata):
        return os.path.join(appdata, "About Time", "settings.json")
    fallback = os.path.join(os.path.expanduser("~"), "About Time", "settings.json")
    print(f"[About Time] APPDATA invalid or missing, using fallback: {fallback}", file=sys.stderr)
    return fallback

_SETTINGS_PATH = _resolve_settings_path()
_first_boot = not os.path.exists(_SETTINGS_PATH)

def _load_settings():
    defaults = {"pinned": False, "layout_mode": "stack", "muted": False,
                "window_x": None, "window_y": None, "timers": []}
    try:
        with open(_SETTINGS_PATH) as f:
            data = json.load(f)
        # Migration from the pre-per-timer-sound settings format: the old
        # global "sound" becomes the fallback default for timers that don't
        # carry their own "sound"/"notify" yet. An old global "mute" maps to
        # every such timer defaulting to off (None) — there's no separate
        # global mute concept anymore, muting a timer just means it has no
        # sound chosen.
        legacy_sound = data.get("sound")
        if legacy_sound in ("short", "medium", "long"):
            legacy_timer_sound_default = legacy_sound
        elif legacy_sound == "mute":
            legacy_timer_sound_default = None
        else:
            legacy_timer_sound_default = "short"
        legacy_notify_default = bool(data.get("notifications", False))
        raw_titles = data.get("titles")
        titles = raw_titles[:MAX_TIMERS] if isinstance(raw_titles, list) else None
        win_x = data.get("window_x")
        win_y = data.get("window_y")
        if not isinstance(win_x, int) or not isinstance(win_y, int):
            win_x = win_y = None
        layout_mode = data.get("layout_mode", defaults["layout_mode"])
        if layout_mode not in ("stack", "row"):
            layout_mode = defaults["layout_mode"]
        # New format: list of per-timer dicts. Fall back to legacy "titles" list.
        raw_timers = data.get("timers")
        if isinstance(raw_timers, list):
            timer_data = []
            for t in raw_timers[:MAX_TIMERS]:
                if not isinstance(t, dict):
                    continue
                title = t.get("title", "")
                if not isinstance(title, str):
                    title = ""
                dur = t.get("duration", 15 * 60)
                if not isinstance(dur, int) or not (1 <= dur <= MAX_DURATION_SECONDS):
                    dur = 15 * 60
                rem = t.get("remaining", dur)
                if not isinstance(rem, int) or not (0 <= rem <= dur):
                    rem = dur
                state = t.get("state", "idle")
                if state not in ("idle", "running", "paused", "finished"):
                    state = "idle"
                # "sound" absent entirely = pre-per-timer file, inherit the old
                # global default. Present (even null, meaning "off") = honor it,
                # falling back only if it's some other invalid value.
                if "sound" in t:
                    snd = t.get("sound")
                    if snd is not None and snd not in ("short", "medium", "long"):
                        snd = legacy_timer_sound_default
                else:
                    snd = legacy_timer_sound_default
                if "notify" in t:
                    ntf = t.get("notify")
                    if not isinstance(ntf, bool):
                        ntf = legacy_notify_default
                else:
                    ntf = legacy_notify_default
                timer_data.append({"title": title, "duration": dur, "remaining": rem,
                                    "state": state, "sound": snd, "notify": ntf})
        else:
            # Migrate from legacy "titles" list
            timer_data = [{"title": t if isinstance(t, str) else "", "duration": 15 * 60,
                           "sound": legacy_timer_sound_default, "notify": legacy_notify_default}
                          for t in (titles or [""])]
        return {
            "pinned":        bool(data.get("pinned", defaults["pinned"])),
            "layout_mode":   layout_mode,
            "muted":         bool(data.get("muted", defaults["muted"])),
            "window_x":      win_x,
            "window_y":      win_y,
            "timers":        timer_data,
        }
    except Exception as e:
        print(f"[About Time] _load_settings failed: {e}", file=sys.stderr)
        return defaults

def _save_settings():
    tmp = _SETTINGS_PATH + ".tmp"
    try:
        os.makedirs(os.path.dirname(_SETTINGS_PATH), exist_ok=True)
        with open(tmp, "w") as f:
            json.dump({
                "pinned":        topmost_var.get(),
                "layout_mode":   _layout_mode,
                "muted":         _muted,
                "window_x":      root.winfo_x(),
                "window_y":      root.winfo_y(),
                "timers":        [{"title": tw.title_entry.get() if tw.title_entry.get() != _TITLE_PLACEHOLDER else "",
                                   "duration": tw.duration_seconds,
                                   "remaining": tw.remaining_seconds,
                                   "state": tw.state,
                                   "sound": tw.sound_mode,
                                   "notify": tw.notify_enabled}
                                  for (_, tw) in timers],
            }, f, indent=2)
        os.replace(tmp, _SETTINGS_PATH)
    except Exception as e:
        print(f"[About Time] _save_settings failed: {e}", file=sys.stderr)
        try:
            os.remove(tmp)
        except OSError:
            pass

_s = _load_settings()
_pinned         = _s["pinned"]
_layout_mode    = _s["layout_mode"]
_win_x          = _s.get("window_x")
_win_y          = _s.get("window_y")

# Global mute is a playback gate, not a data mutation — each timer's own
# sound_mode is left untouched while muted, so there's nothing to stash and
# restore. _muted itself is persisted (see _save_settings/_load_settings).
_muted = _s.get("muted", False)

def beep(sound_mode):
    """Plays the given timer's own chosen sound, unless muted globally or it
    has none chosen (sound_mode is None)."""
    if _muted or not sound_mode:
        return
    with _wav_lock:
        wav = _WAVS.get(sound_mode)
    threading.Thread(target=_play, args=(wav,), daemon=True).start()

def notify(title, duration):
    """Shows a Windows toast when a timer finishes.

    The timer title is free-form user text. winotify embeds a toast's msg
    into a PowerShell double-quoted here-string, which PowerShell expands
    (e.g. "$(...)" runs as code) before that text ever becomes toast XML —
    the CDATA wrapper only protects the XML layer, not the PowerShell layer
    underneath it. So the title's content is never written into the script
    as literal text at all: it's Base64-encoded here and decoded back to a
    string by a fixed, title-independent PowerShell expression, with only
    that resulting variable referenced in the template. Whatever the title
    contains, it can only ever end up as inert data to PowerShell.
    """
    if sys.platform != "win32":
        return
    safe_title = str(title)[:50].strip() if title else ""
    msg = f"Your timer '{safe_title}' has finished." if safe_title else f"Your {duration} timer has finished."
    msg_b64 = base64.b64encode(msg.encode("utf-8")).decode("ascii")

    def _send():
        toast = Notification(app_id="About Time", title="Timer finished", msg="$Msg")
        toast.actions = ""
        toast.audio = '<audio silent="true" />'
        script = (
            f'$MsgB64 = "{msg_b64}"\n'
            "$Msg = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($MsgB64))\n"
            + _TOAST_TEMPLATE.format(**toast.__dict__)
        )
        _toast_run_ps(command=script)
    threading.Thread(target=_send, daemon=True).start()

# ── Helpers ────────────────────────────────────────────────────────────────────
def fmt(seconds):
    if seconds >= 86400:
        d = seconds // 86400
        rem = seconds % 86400
        h = rem // 3600
        m = (rem % 3600) // 60
        s = rem % 60
        return f"{d}d {h}:{m:02d}:{s:02d}"
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
    day_match = re.match(r"^(\d+)d\s+(\d{1,2}):(\d{2}):(\d{2})$", text, re.IGNORECASE)
    if day_match:
        days, h, m, s = (int(x) for x in day_match.groups())
        if not (0 <= m <= 59 and 0 <= s <= 59):
            return None
        total = days * 86400 + h * 3600 + m * 60 + s
        return total if 1 <= total <= MAX_DURATION_SECONDS else None
    if ":" in text:
        parts = text.split(":")
        try:
            nums = [int(p) for p in parts]
        except ValueError:
            return None
        if len(parts) == 2:
            m, s = nums
            if not (m >= 0 and 0 <= s <= 59):
                return None
            total = m * 60 + s
        elif len(parts) == 3:
            h, m, s = nums
            if not (h >= 0 and 0 <= m <= 59 and 0 <= s <= 59):
                return None
            total = h * 3600 + m * 60 + s
        else:
            return None
        return total if 1 <= total <= MAX_DURATION_SECONDS else None
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
    elif unit.startswith("d"):
        total = value * 86400
    else:
        return None
    return total if 1 <= total <= MAX_DURATION_SECONDS else None


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
    def __init__(self, parent, deletable=False, on_delete=None, initial_title="",
                 initial_duration=15 * 60, initial_remaining=None, initial_state="idle",
                 initial_sound="short", initial_notify=False, **kwargs):
        super().__init__(parent, fg_color="transparent", border_width=0, corner_radius=8,
                          width=TIMER_W, height=TIMER_H, **kwargs)
        self.pack_propagate(False)  # force fixed size regardless of content/state
        self.duration_seconds = initial_duration
        self.sound_mode = initial_sound      # "short"/"medium"/"long"/None — this timer's own choice
        self.notify_enabled = initial_notify  # this timer's own Windows-toast opt-in
        self.after_id = None
        self.editing_countdown = False
        self.edit_var = ctk.StringVar()
        # Restore mid-run timers as paused; treat finished as idle
        if initial_state in ("running", "paused") and initial_remaining is not None and initial_remaining > 0:
            self.remaining_seconds = initial_remaining
            self.state = "idle"
            self.last_valid_display = fmt(initial_duration)
            self.display_var = ctk.StringVar(value=fmt(initial_remaining))
            self._build(deletable, on_delete, initial_title)
            root.after_idle(lambda: self._set_state("paused"))
        else:
            self.remaining_seconds = initial_duration
            self.state = "idle"
            self.last_valid_display = fmt(initial_duration)
            self.display_var = ctk.StringVar(value=fmt(initial_duration))
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
            # Top-right, not top-left — a close button on the left edge of a
            # box reads as belonging to whatever's to ITS left (the previous
            # timer), not the box it's actually attached to, especially in
            # row mode where boxes sit side by side. Top-right is also the
            # conventional close-button corner (title bars, browser tabs).
            del_btn.place(relx=1.0, anchor="ne", x=-4, y=4)

            del_tip = ctk.CTkLabel(
                self, text="Remove this timer",
                fg_color=("#4a4a4a", "#2a2a2a"),
                corner_radius=4,
                font=ctk.CTkFont(size=16),
            )
            # Tooltip opens toward the box's interior (leftward from the
            # button), mirroring the button's own corner flip.
            del_btn.bind("<Enter>", lambda e: (del_tip.place(relx=1.0, anchor="ne", x=-34, y=7), del_tip.lift()))
            del_btn.bind("<Leave>", lambda e: del_tip.place_forget())

        self.title_entry = ctk.CTkEntry(
            self,
            border_width=0,
            fg_color="transparent",
            font=ctk.CTkFont(size=16),
            justify="center",
        )
        if initial_title:
            self.title_entry.insert(0, initial_title)
            self.title_entry.configure(text_color=_TITLE_TEXT_COLOR)
        else:
            self.title_entry.insert(0, _TITLE_PLACEHOLDER)
            self.title_entry.configure(text_color=_TITLE_PLACEHOLDER_COLOR)
        self.title_entry.pack(fill="x", padx=(36, 36), pady=(6, 0))
        self.title_entry.bind("<FocusIn>",  self._title_focus_in)
        self.title_entry.bind("<FocusOut>", self._title_focus_out)
        self.title_entry.bind("<Return>", lambda e: self.focus())

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

        # Per-timer sound + notification row — this timer's own choice, independent
        # of every other timer. Sound is mutually exclusive (clicking the active
        # icon again turns this timer's sound off) and separate from the global
        # mute, which silences every timer regardless of its own choice.
        self.extra_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.extra_frame.pack(pady=(0, 6))

        self._sound_btns = {}
        for _sym, _mode in (("♪", "short"), ("♫", "medium"), ("♬", "long")):
            _sbtn = ctk.CTkButton(
                self.extra_frame, text=_sym, width=26, height=26,
                font=ctk.CTkFont(size=14),
                fg_color="transparent",
                hover_color=("#3a3a4a", "#3a3a4a"),
                command=lambda m=_mode: self._toggle_sound(m),
            )
            _sbtn.pack(side="left", padx=2)
            self._sound_btns[_mode] = _sbtn

        self.notify_btn = ctk.CTkButton(
            self.extra_frame, text="🔔", width=26, height=26,
            # A literal bell reads far clearer than "!" for what this
            # actually toggles. It's technically an emoji codepoint (this
            # app otherwise avoids those), but confirmed rendering flat and
            # monochrome here — no colored-glyph clash with the rest of the
            # icon set. Bold weight kept as backup if a future font/system
            # renders it thin.
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="transparent",
            hover_color=("#3a3a4a", "#3a3a4a"),
            command=self._toggle_notify,
        )
        self.notify_btn.pack(side="left", padx=2)

        self._update_sound_btns()
        self._update_notify_btn()

        self._set_state("idle")

    # ── Per-timer sound / notification ────────────────────────────────────────
    def _toggle_sound(self, mode):
        if _muted:
            # belt and braces: the buttons are disabled while muted (see
            # toggle_mute), but this guard means it's a true no-op even if
            # called some other way — no route to "muted but one timer
            # quietly has sound on anyway".
            return
        self.sound_mode = None if self.sound_mode == mode else mode
        self._update_sound_btns()
        if self.sound_mode:
            beep(self.sound_mode)
        _save_settings()

    def _update_sound_btns(self):
        for m, btn in self._sound_btns.items():
            btn.configure(fg_color=("#1F6AA5", "#1F6AA5") if m == self.sound_mode else "transparent")

    def _toggle_notify(self):
        self.notify_enabled = not self.notify_enabled
        self._update_notify_btn()
        _save_settings()

    def _update_notify_btn(self):
        self.notify_btn.configure(fg_color=("#1F6AA5", "#1F6AA5") if self.notify_enabled else "transparent")

    # ── Title placeholder ──────────────────────────────────────────────────────
    def _title_focus_in(self, event=None):
        if self.title_entry.get() == _TITLE_PLACEHOLDER:
            self.title_entry.delete(0, "end")
            self.title_entry.configure(text_color=_TITLE_TEXT_COLOR)

    def _title_focus_out(self, event=None):
        if self.title_entry.get() == "":
            self.title_entry.insert(0, _TITLE_PLACEHOLDER)
            self.title_entry.configure(text_color=_TITLE_PLACEHOLDER_COLOR)
        _save_settings()

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
            beep(self.sound_mode)
            if self.notify_enabled:
                _title = self.title_entry.get().strip()
                notify(_title if _title != _TITLE_PLACEHOLDER else "", self.last_valid_display)
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
            self.start_btn.pack(padx=4, pady=2, anchor="center")
        elif new_state == "running":
            self.restart_btn.configure(width=BTN_W)
            self.restart_btn.pack(side="left", padx=4, pady=2, anchor="center")
            self.pause_btn.pack(side="left", padx=4, pady=2, anchor="center")
        elif new_state == "paused":
            btn_w = max(40, (TIMER_W - 70) // 3)
            for btn in (self.stop_btn, self.resume_btn, self.restart_btn):
                btn.configure(width=btn_w)
                btn.pack(side="left", padx=2, pady=2, anchor="center")
        elif new_state == "finished":
            self.restart_btn.configure(width=BTN_W)
            self.restart_btn.pack(padx=4, pady=2, anchor="center")
        # Absorb any required-height change so buttons are never squashed.
        # During _build the widget isn't packed yet; add_timer fits afterwards.
        if any(t is self for _s, t in timers):
            _fit_window_any(preserve=True)

    # ── Countdown click-to-edit ────────────────────────────────────────────────
    def _on_countdown_click(self, event=None):
        if self.editing_countdown:
            return
        self.editing_countdown = True
        self.edit_var.set(self.last_valid_display if self.state == "finished" else self.display_var.get())
        self.countdown_label.pack_forget()
        self.countdown_entry.pack(padx=8, pady=1)
        _fit_window_any(preserve=True)
        self.countdown_entry.focus()
        self.countdown_entry.after(10, lambda: self.countdown_entry.select_range(0, "end"))

    def _commit_countdown(self, event=None):
        if not self.editing_countdown:
            return
        self.editing_countdown = False
        text = self.edit_var.get()
        seconds = parse_input(text)
        self.countdown_entry.pack_forget()
        self.countdown_label.pack(padx=8, pady=1)
        _fit_window_any(preserve=True)
        if seconds is None:
            if self.state in ("running", "paused"):
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
_base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
root.iconbitmap(os.path.join(_base, "assets", "icon.ico"))
root.resizable(True, True)
root.minsize(250, 130)

# ── Timers ─────────────────────────────────────────────────────────────────────
timers_frame = ctk.CTkFrame(root, fg_color="transparent")
timers_frame.pack(fill="x")

# Each entry is (separator_or_None, TimerWidget)
timers = []

def _make_separator():
    """Creates a separator oriented for the current _layout_mode: a horizontal
    line between stacked rows, or a vertical line between side-by-side boxes."""
    if _layout_mode == "stack":
        sep = ctk.CTkFrame(timers_frame, height=1, fg_color=("#444444", "#333333"))
        sep.pack(fill="x", padx=12, pady=2)
    else:
        # height must be set explicitly — CTkFrame defaults to 200px tall
        # when unset, which silently overrides every box's row height (via
        # pack sizing the row to its tallest child) far past what any of
        # them actually need, showing up as dead space above/below every
        # timer in row mode. Sized so height + its own pady lands on exactly
        # TIMER_H, matching a TimerWidget box's own (pady-less) cell height —
        # otherwise the padding alone re-introduces the same overshoot.
        _sep_pady = 12
        sep = ctk.CTkFrame(timers_frame, width=1, height=TIMER_H - 2 * _sep_pady,
                            fg_color=("#444444", "#333333"))
        sep.pack(side="left", fill="y", padx=2, pady=_sep_pady)
    return sep

def _pack_timer(tw):
    """Packs a TimerWidget for the current _layout_mode — top-to-bottom stack,
    or left-to-right row. Box size itself is fixed (TIMER_W x TIMER_H, forced
    via pack_propagate(False) in TimerWidget), so fill/expand here are purely
    about placement, not sizing."""
    if _layout_mode == "stack":
        tw.pack(fill="x")
    else:
        tw.pack(side="left")

def add_timer(deletable=False, initial_title="", initial_duration=15 * 60, initial_remaining=None,
              initial_state="idle", initial_sound="short", initial_notify=False):
    if len(timers) >= MAX_TIMERS:
        return
    sep = _make_separator() if timers else None
    tw = TimerWidget(timers_frame, deletable=deletable, on_delete=lambda: remove_timer(tw),
                     initial_title=initial_title, initial_duration=initial_duration,
                     initial_remaining=initial_remaining, initial_state=initial_state,
                     initial_sound=initial_sound, initial_notify=initial_notify)
    _pack_timer(tw)
    timers.append((sep, tw))
    if _muted:
        # a timer opened while globally muted should show as muted too —
        # its own sound_mode is left as-is, only the controls grey out.
        _set_sound_controls_enabled(tw, False)
    _update_add_btn()
    _fit_window_any()
    _save_settings()

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
    _update_add_btn()
    _fit_window_any()
    _save_settings()

def _fit_window(preserve=False, width=None):
    """Stack-mode sizing: pins the window to its exact required size, the
    same fixed-size/no-drag-resize behavior as row mode — every timer is
    always fully shown, no partial-reveal collapse.

    `preserve`/`width` are accepted so every existing call site still works
    unchanged, but neither has anything left to do now that there's a
    single fixed target instead of a range of collapse points."""
    root.update_idletasks()
    w = root.winfo_reqwidth()
    h = root.winfo_reqheight()
    root.minsize(w, h)
    root.maxsize(w, h)
    root.geometry(f"{w}x{h}")

def _fit_window_row():
    """Row-mode sizing: every box is fixed-size and all timers are always
    shown, so the window is just locked to exactly fit its natural required
    size — min and max pinned equal, so there's nothing for the user to
    drag-resize into."""
    root.update_idletasks()
    w = root.winfo_reqwidth()
    h = root.winfo_reqheight()
    root.minsize(w, h)
    root.maxsize(w, h)
    root.geometry(f"{w}x{h}")

def _fit_window_any(preserve=False, width=None):
    """Mode-aware dispatcher — use this from anywhere that isn't already
    known to be stack-only (e.g. TimerWidget state changes, add/remove)."""
    if _layout_mode == "stack":
        _fit_window(preserve=preserve, width=width)
    else:
        _fit_window_row()

# ── Layout mode toggle (stack ↔ row) ────────────────────────────────────────────
def _relayout_timers():
    """Re-packs every existing TimerWidget (and rebuilds separators) for
    whatever _layout_mode currently is. TimerWidgets themselves are never
    destroyed/recreated — only their pack placement and the separators
    between them change."""
    old = list(timers)
    timers.clear()
    for i, (old_sep, tw) in enumerate(old):
        if old_sep:
            old_sep.pack_forget()
            old_sep.destroy()
        tw.pack_forget()
        sep = _make_separator() if i > 0 else None
        _pack_timer(tw)
        timers.append((sep, tw))

def _toggle_layout_mode():
    global _layout_mode
    _layout_mode = "row" if _layout_mode == "stack" else "stack"
    _relayout_timers()
    if _layout_mode == "stack":
        # row mode's leftover width is meaningless in a single-column
        # layout — snap to the new stack's own natural width instead of
        # preserving it (see _fit_window's `width` param)
        root.update_idletasks()
        new_width = root.winfo_reqwidth()
    else:
        new_width = None
    _fit_window_any(width=new_width)
    _update_layout_btn()
    _save_settings()
    if _layout_tip.winfo_ismapped():
        _show_layout_tip()

# ── Add timer button ───────────────────────────────────────────────────────────
add_btn_frame = ctk.CTkFrame(root, fg_color="transparent")
add_btn_frame.pack(fill="x", pady=(0, 6))

add_btn = ctk.CTkButton(
    add_btn_frame,
    text="◓ Add timer",
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
        add_btn.configure(text=f"{_CLOCK_SYMS[len(timers)]} Add timer")
        add_btn_frame.pack(fill="x", pady=(0, 6))

# ── Pin / Always on top ────────────────────────────────────────────────────────
topmost_var = ctk.BooleanVar(value=_pinned)

def toggle_topmost():
    topmost_var.set(not topmost_var.get())
    root.wm_attributes("-topmost", topmost_var.get())
    _update_pin()
    _save_settings()
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
    font=ctk.CTkFont(size=16, weight="bold"),
    fg_color="transparent",
    hover_color=("#3a3a4a", "#3a3a4a"),
    command=toggle_topmost,
)
pin_btn.place(x=4, y=4)
pin_btn.bind("<Enter>", _show_tip)
pin_btn.bind("<Leave>", _hide_tip)

# ── Volume control ─────────────────────────────────────────────────────────────
# One button, not two — clicking it pops a slider that reads/writes this
# process's own Windows Volume Mixer session directly (see _get_app_volume/
# _set_app_volume above). There's no in-app volume state anymore: Windows
# is the only volume control, exactly as it would be for any other app.
_vol_popup_open = False

def _on_volume_slider_change(value):
    _set_app_volume(float(value) / 100.0)

def _show_volume_popup():
    global _vol_popup_open
    current = _get_app_volume()
    volume_slider.set(100.0 if current is None else current * 100)
    volume_popup.place(x=64, y=4)
    volume_popup.lift()
    _vol_popup_open = True

def _hide_volume_popup():
    global _vol_popup_open
    volume_popup.place_forget()
    _vol_popup_open = False

def _toggle_volume_popup():
    _hide_volume_popup() if _vol_popup_open else _show_volume_popup()

volume_popup = ctk.CTkFrame(root, fg_color=("#2a2a2a", "#2a2a2a"), corner_radius=6)
volume_slider = ctk.CTkSlider(volume_popup, from_=0, to=100, width=120,
                               command=_on_volume_slider_change)
volume_slider.pack(padx=10, pady=8)

volume_btn = ctk.CTkButton(
    root, text="🔊", width=26, height=26,
    font=ctk.CTkFont(size=16, weight="bold"),  # matches the rest of the corner column
    fg_color="transparent",
    hover_color=("#3a3a4a", "#3a3a4a"),
    command=_toggle_volume_popup,
)
volume_btn.place(x=34, y=4)

# ── Global mute ────────────────────────────────────────────────────────────────
# A real toggle over every timer's own sound choice, not a separate hidden
# override: muting blanks each timer's sound (and its icon row reflects
# that, same as if the user had clicked each one off individually), and
# remembers what each was set to so unmuting can restore it. While muted,
# each timer's sound icons are disabled outright — deliberately, so there's
# no way to end up with global mute showing "on" while some individual timer
# is quietly making sound again. A timer opened while already muted (see
# add_timer) is treated the same as one that was already there.
def _set_sound_controls_enabled(tw, enabled):
    state = "normal" if enabled else "disabled"
    for btn in tw._sound_btns.values():
        btn.configure(state=state)

def toggle_mute():
    global _muted
    _muted = not _muted
    for _, tw in timers:
        _set_sound_controls_enabled(tw, not _muted)
    _update_mute_btn()
    _save_settings()
    if _mute_tip.winfo_ismapped():
        _show_mute_tip()

def _update_mute_btn():
    mute_btn.configure(fg_color=("#1F6AA5", "#1F6AA5") if _muted else "transparent")

_mute_tip = _make_tip()

def _show_mute_tip(event=None):
    _mute_tip.configure(text="Muted: On" if _muted else "Muted: Off")
    _mute_tip.place(x=34, y=117)

def _hide_mute_tip(event=None):
    _mute_tip.place_forget()

mute_btn = ctk.CTkButton(
    root, text="🔇", width=26, height=26,  # a real muted-speaker glyph, not a generic "no" circle
    font=ctk.CTkFont(size=16, weight="bold"),  # matches the rest of the corner column
    fg_color="transparent",
    hover_color=("#3a3a4a", "#3a3a4a"),
    command=toggle_mute,
)
mute_btn.place(x=4, y=114)  # aligned with every timer's own icon row (measured), not the corner column
mute_btn.bind("<Enter>", _show_mute_tip)
mute_btn.bind("<Leave>", _hide_mute_tip)

# ── Layout mode toggle button ──────────────────────────────────────────────────
_layout_tip = _make_tip()

def _show_layout_tip(event=None):
    _layout_tip.configure(text="Switch to stack layout" if _layout_mode == "row" else "Switch to row layout")
    _layout_tip.place(x=34, y=35)

def _hide_layout_tip(event=None):
    _layout_tip.place_forget()

def _update_layout_btn():
    # Icon shows where clicking takes you, not the current state — in stack
    # mode a right arrow means "swap to row"; in row mode a down arrow means
    # "back to column". No highlight color for this one, deliberately —
    # unlike pin/mute (a real on/off state), stack and row are two equally
    # valid modes, not an "active" state worth calling out.
    layout_btn.configure(text="↓" if _layout_mode == "row" else "→")

layout_btn = ctk.CTkButton(
    root, text="→", width=26, height=26,
    font=ctk.CTkFont(size=16, weight="bold"),
    fg_color="transparent",
    hover_color=("#3a3a4a", "#3a3a4a"),
    command=_toggle_layout_mode,
)
layout_btn.place(x=4, y=32)
layout_btn.bind("<Enter>", _show_layout_tip)
layout_btn.bind("<Leave>", _hide_layout_tip)

# ── Init — apply persisted settings ───────────────────────────────────────────
_build_wavs()
_prime_audio_session()
root.wm_attributes("-topmost", topmost_var.get())
_update_pin()
_update_mute_btn()
_update_layout_btn()

if _first_boot:
    add_timer(deletable=False, initial_title="About Time")
else:
    _saved_timers = _s.get("timers") or [{"title": "", "duration": 15 * 60}]
    for i, _t in enumerate(_saved_timers):
        title = _t["title"] if (i > 0 or _t["title"]) else "About Time"
        add_timer(deletable=(i > 0), initial_title=title,
                  initial_duration=_t["duration"],
                  initial_remaining=_t.get("remaining"),
                  initial_state=_t.get("state", "idle"),
                  initial_sound=_t.get("sound", "short"),
                  initial_notify=_t.get("notify", False))

def _on_close():
    _save_settings()
    root.destroy()

def _on_global_click(event):
    """Closes the volume popup on any click outside it — its own toggle
    button and the slider inside it are excluded so opening/dragging still
    works normally. CTkButton/CTkSlider are composite widgets (an internal
    canvas etc.), so event.widget is rarely the button/frame object itself —
    walking up .master is required, not just comparing the raw event widget."""
    if not _vol_popup_open:
        return
    w = event.widget
    while w is not None:
        if w is volume_btn or w is volume_popup:
            return
        w = w.master
    _hide_volume_popup()

root.bind_all("<Button-1>", _on_global_click, add="+")
root.protocol("WM_DELETE_WINDOW", _on_close)

# Restore window position — loose bounds allow multi-monitor layouts (negative or large x/y)
if _win_x is not None and _win_y is not None:
    if -32000 <= _win_x <= 32000 and -32000 <= _win_y <= 32000:
        root.geometry(f"+{_win_x}+{_win_y}")

root.mainloop()
