"""
Tests for notify() — the Windows-toast trigger, and specifically the
Base64-encode/decode indirection that keeps a free-form timer title from
ever being written into the PowerShell script as literal text (the fix for
the toast-notification RCE — see about_time.py's notify() docstring).

Same source-extraction strategy as test_about_time.py: notify() itself has
no GUI dependency, but it does spawn a background thread that shells out to
PowerShell via _toast_run_ps. Both threading.Thread and _toast_run_ps are
replaced with synchronous test doubles here so no real thread or process is
ever created — the "script" that would have been run is just captured for
inspection instead.
"""

import base64
import re
import types
from pathlib import Path

import pytest
from winotify import Notification, TEMPLATE as _TOAST_TEMPLATE, _run_ps

SRC_PATH = Path(__file__).parent.parent / "about_time.py"
SOURCE = SRC_PATH.read_text(encoding="utf-8")


class _SyncThread:
    """Stand-in for threading.Thread that runs its target immediately and
    synchronously on .start(), so tests don't need to poll/join a real
    background thread just to observe what it would have sent to
    PowerShell."""
    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        self._target(*self._args, **self._kwargs)


@pytest.fixture()
def notify_env():
    calls = []
    match = re.search(
        r"(^def notify\(title, duration\):.*?)(?=^# ── Helpers)",
        SOURCE, re.DOTALL | re.MULTILINE,
    )
    assert match, "notify() not found in source"
    ns = {
        "__builtins__": __builtins__,
        "sys": types.SimpleNamespace(platform="win32"),
        "base64": base64,
        "threading": types.SimpleNamespace(Thread=_SyncThread),
        "Notification": Notification,
        "_TOAST_TEMPLATE": _TOAST_TEMPLATE,
        "_toast_run_ps": lambda command: calls.append(command),
    }
    exec(match.group(1), ns)
    ns["_ps_calls"] = calls
    return ns


def _decode_msg(script):
    """Pulls the $MsgB64 literal out of a captured script and decodes it,
    mirroring exactly what the real PowerShell expression does."""
    b64 = re.search(r'\$MsgB64 = "([^"]*)"', script).group(1)
    return base64.b64decode(b64).decode("utf-8")


class TestNotifyMessageContent:
    def test_titled_timer_message(self, notify_env):
        notify_env["notify"]("Laundry", "25:00")
        script = notify_env["_ps_calls"][0]
        assert _decode_msg(script) == "Your timer 'Laundry' has finished."

    def test_untitled_timer_falls_back_to_duration(self, notify_env):
        notify_env["notify"]("", "25:00")
        script = notify_env["_ps_calls"][0]
        assert _decode_msg(script) == "Your 25:00 timer has finished."

    def test_none_title_falls_back_to_duration(self, notify_env):
        notify_env["notify"](None, "1:00:00")
        script = notify_env["_ps_calls"][0]
        assert _decode_msg(script) == "Your 1:00:00 timer has finished."

    def test_title_truncated_to_50_chars(self, notify_env):
        long_title = "x" * 80
        notify_env["notify"](long_title, "5:00")
        script = notify_env["_ps_calls"][0]
        assert _decode_msg(script) == f"Your timer '{'x' * 50}' has finished."

    def test_whitespace_only_title_treated_as_untitled(self, notify_env):
        notify_env["notify"]("   ", "5:00")
        script = notify_env["_ps_calls"][0]
        assert _decode_msg(script) == "Your 5:00 timer has finished."

    def test_non_windows_platform_does_nothing(self, notify_env):
        notify_env["sys"].platform = "linux"
        notify_env["notify"]("Laundry", "25:00")
        assert notify_env["_ps_calls"] == []


class TestNotifyTitleNeverLiteralInScript:
    """The actual security regression: a malicious title must never appear
    as literal text anywhere in the generated PowerShell script — only as
    Base64 data, which PowerShell treats as an inert string, never code."""

    @pytest.mark.parametrize("payload", [
        "$(calc.exe)",
        "`whoami`",
        '"; Remove-Item C:\\ -Recurse -Force; "',
        "$(sc 'C:\\Users\\test\\Desktop\\h.txt' 'Hello World')",
        "'; DROP TABLE timers; --",
    ])
    def test_malicious_title_not_literal_in_script(self, notify_env, payload):
        notify_env["notify"](payload, "5:00")
        script = notify_env["_ps_calls"][0]

        assert payload not in script
        assert _decode_msg(script) == f"Your timer '{payload[:50]}' has finished."
