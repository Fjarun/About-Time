"""
Tests for notify() — the Windows-toast trigger, and specifically the
Base64-encode/decode indirection that keeps a free-form timer title from
ever being written into the PowerShell script as literal text (the fix for
the toast-notification RCE — see about_time.py's notify() docstring).

Same source-extraction strategy as test_about_time.py: notify() itself has
no GUI dependency, but it does spawn a background thread that shells out to
PowerShell via _run_toast_script. Both threading.Thread and _run_toast_script are
replaced with synchronous test doubles here so no real thread or process is
ever created — the "script" that would have been run is just captured for
inspection instead.
"""

import base64
import os
import re
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest
from winotify import Notification, TEMPLATE as _TOAST_TEMPLATE

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
        r"(^def _toast_safe\(text\):.*?)(?=^# ── Helpers)",
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
        "os": os,
        "re": re,
        "subprocess": subprocess,
    }
    exec(match.group(1), ns)
    ns["_run_toast_script"] = lambda script: calls.append(script)
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


class TestToastXmlSafety:
    """A title containing the CDATA terminator must not be able to close the
    CDATA section and inject toast XML."""




class TestRunToastScript:
    """PowerShell is launched by absolute System32 path, not PATH lookup."""

    def test_uses_absolute_system32_powershell(self, monkeypatch):
        seen = {}

        class _FakeSI:
            dwFlags = 0

        monkeypatch.setattr(subprocess, "STARTUPINFO", _FakeSI, raising=False)
        monkeypatch.setattr(subprocess, "STARTF_USESHOWWINDOW", 1, raising=False)
        monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: seen.update(cmd=cmd))
        monkeypatch.setenv("SystemRoot", r"C:\Windows")
        match = re.search(r"(^def _run_toast_script\(script\):.*?)(?=^def notify)",
                          SOURCE, re.DOTALL | re.MULTILINE)
        ns = {"os": os, "subprocess": subprocess}
        exec(match.group(1), ns)
        ns["_run_toast_script"]("echo hi")
        exe = seen["cmd"][0]
        assert os.path.isabs(exe)
        assert exe.lower().endswith(r"system32\windowspowershell\v1.0\powershell.exe")
        assert seen["cmd"][-1] == "echo hi"


class TestWinotifyTemplateAssumptions:
    """notify()'s injection fix only holds if winotify's template still puts
    {msg} as plain text inside a PowerShell @"..."@ here-string, wrapped in
    CDATA. Fails loudly if a winotify upgrade changes that layout, instead of
    toasts silently breaking or the "$Msg" placeholder behaving differently."""

    def test_msg_is_cdata_inside_here_string(self):
        start = _TOAST_TEMPLATE.index('@"')
        end = _TOAST_TEMPLATE.index('"@', start + 2)
        here_string = _TOAST_TEMPLATE[start:end]
        assert "<![CDATA[{msg}]]>" in here_string

    def test_msg_appears_exactly_once(self):
        assert _TOAST_TEMPLATE.count("{msg}") == 1

    def test_pinned_winotify_version(self):
        from importlib.metadata import version
        pinned = re.search(r"^winotify==(\S+)", (SRC_PATH.parent / "requirements.txt").read_text(), re.M).group(1)
        assert version("winotify") == pinned


class TestToastSafe:
    @pytest.mark.parametrize("bad", ["\ud800", "\udbff", "\udc00", "\udfff", "\ufffe", "\uffff"])
    def test_surrogates_and_noncharacters_stripped(self, notify_env, bad):
        assert notify_env["_toast_safe"](f"a{bad}b") == "ab"

    def test_c0_controls_stripped_but_tab_newline_cr_kept_inside(self, notify_env):
        out = notify_env["_toast_safe"]("a\x00\x08\x0b\x0c\x0e\x1fb\tc\nd\re")
        assert out == "ab\tc\nd\re"

    def test_cdata_terminator_defused_exactly(self, notify_env):
        assert notify_env["_toast_safe"]("a]]>b") == "a] ] >b"

    def test_result_has_no_cdata_terminator_even_when_repeated(self, notify_env):
        assert "]]>" not in notify_env["_toast_safe"]("]]>]]>]]]>")

    def test_terminator_split_by_stripped_char_is_still_defused(self, notify_env):
        # stripping happens first, so "]]\x00>" collapses to "]]>" and must be defused
        out = notify_env["_toast_safe"]("]]\x00>")
        assert "]]>" not in out

    def test_result_is_stripped(self, notify_env):
        assert notify_env["_toast_safe"]("  \t hi \n ") == "hi"

    def test_empty_string_returns_empty(self, notify_env):
        assert notify_env["_toast_safe"]("") == ""

    def test_only_illegal_chars_returns_empty(self, notify_env):
        assert notify_env["_toast_safe"]("\x00\ud800\uffff") == ""

    def test_result_is_utf8_encodable(self, notify_env):
        out = notify_env["_toast_safe"]("x\ud83d\ude00y")  # lone surrogate pair halves
        assert out.encode("utf-8") == b"xy"

    def test_normal_unicode_preserved(self, notify_env):
        assert notify_env["_toast_safe"]("caf\u00e9 \u65e5\u672c") == "caf\u00e9 \u65e5\u672c"

    def test_non_string_raises_type_error(self, notify_env):
        with pytest.raises(TypeError):
            notify_env["_toast_safe"](None)


class TestNotifySurrogateTitle:
    def test_surrogate_title_does_not_raise_and_message_decodes(self, notify_env):
        notify_env["notify"]("bad\ud800title", "5:00")
        msg = _decode_msg(notify_env["_ps_calls"][0])
        assert msg == "Your timer 'badtitle' has finished."

    def test_surrogate_only_title_falls_back_to_duration(self, notify_env):
        notify_env["notify"]("\ud800\udfff", "5:00")
        assert _decode_msg(notify_env["_ps_calls"][0]) == "Your 5:00 timer has finished."

    def test_noncharacter_title_message_is_valid_utf8_base64(self, notify_env):
        notify_env["notify"]("a\uffffb\ufffe", "5:00")
        script = notify_env["_ps_calls"][0]
        b64 = re.search(r'\$MsgB64 = "([^"]*)"', script).group(1)
        assert base64.b64decode(b64, validate=True).decode("utf-8") == "Your timer 'ab' has finished."


class TestRunToastScriptEnvironment:
    @pytest.fixture()
    def run(self, monkeypatch):
        seen = {}

        class _FakeSI:
            dwFlags = 0

        monkeypatch.setattr(subprocess, "STARTUPINFO", _FakeSI, raising=False)
        monkeypatch.setattr(subprocess, "STARTF_USESHOWWINDOW", 1, raising=False)
        monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: seen.update(cmd=cmd))
        match = re.search(r"(^def _run_toast_script\(script\):.*?)(?=^def notify)",
                          SOURCE, re.DOTALL | re.MULTILINE)
        ns = {"os": os, "subprocess": subprocess}
        exec(match.group(1), ns)

        def _go(script="echo hi", windows_dir=None):
            if windows_dir is not None:
                ns["_system_windows_dir"] = lambda: windows_dir
            ns["_run_toast_script"](script)
            return seen["cmd"]
        _go.ns = ns
        return _go

    @staticmethod
    def _ps_path(root):
        return os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")

    def test_os_reported_windows_dir_is_used(self, run, tmp_path):
        assert run(windows_dir=str(tmp_path))[0] == self._ps_path(str(tmp_path))

    def test_systemroot_env_var_is_ignored(self, run, monkeypatch, tmp_path):
        """A11: a same-user env change must not redirect the PowerShell path."""
        monkeypatch.setenv("SystemRoot", str(tmp_path))
        cmd = run()
        assert cmd[0] != self._ps_path(str(tmp_path))
        assert cmd[0] == self._ps_path(run.ns["_system_windows_dir"]() or r"C:\Windows")

    def test_real_os_call_returns_an_existing_directory(self, run):
        d = run.ns["_system_windows_dir"]()
        assert os.path.isabs(d) and os.path.isdir(d)

    @pytest.mark.parametrize("bad", ["", "evil"])
    def test_empty_or_relative_dir_falls_back_to_c_windows(self, run, monkeypatch, tmp_path, bad):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "evil").mkdir()
        assert run(windows_dir=bad)[0] == self._ps_path(r"C:\Windows")

    def test_nonexistent_dir_falls_back_to_c_windows(self, run, tmp_path):
        assert run(windows_dir=str(tmp_path / "does_not_exist"))[0] == self._ps_path(r"C:\Windows")

    def test_dir_pointing_at_file_falls_back_to_c_windows(self, run, tmp_path):
        f = tmp_path / "afile"
        f.write_text("x")
        assert run(windows_dir=str(f))[0] == self._ps_path(r"C:\Windows")

    def test_api_failure_returns_empty_string(self, run, monkeypatch):
        import ctypes
        monkeypatch.setattr(ctypes, "windll", None, raising=False)
        assert run.ns["_system_windows_dir"]() == ""

    def test_command_has_noprofile_and_no_execution_policy_bypass(self, run, monkeypatch):
        monkeypatch.setenv("SystemRoot", r"C:\Windows")
        cmd = run("echo hi")
        assert "-NoProfile" in cmd
        assert "-ExecutionPolicy" not in cmd
        assert not any("bypass" in str(a).lower() for a in cmd)
        assert cmd[-2:] == ["-Command", "echo hi"]


class TestToastFailureLogged:
    """A3: notify() sends on a background thread, where an exception used to
    vanish silently. A failed send must reach _log_error and never raise."""

    def test_failed_send_is_logged_not_raised(self, notify_env):
        logged = []

        def boom(script):
            raise OSError("powershell missing")

        notify_env["_run_toast_script"] = boom
        notify_env["_log_error"] = logged.append
        notify_env["notify"]("Laundry", "25:00")  # must not raise
        assert len(logged) == 1
        assert logged[0].startswith("toast failed:")
        assert "powershell missing" in logged[0]

    def test_successful_send_logs_nothing(self, notify_env):
        logged = []
        notify_env["_log_error"] = logged.append
        notify_env["notify"]("Laundry", "25:00")
        assert logged == []
        assert len(notify_env["_ps_calls"]) == 1


class _FakeProc:
    """Stands in for a finished subprocess.Popen."""
    def __init__(self, returncode=0, stderr="", hang_first=False):
        self.returncode = returncode
        self._stderr = stderr
        self._hang = hang_first
        self.killed = False
        self.communicate_calls = []

    def communicate(self, timeout=None):
        self.communicate_calls.append(timeout)
        if self._hang and timeout is not None:
            self._hang = False
            raise subprocess.TimeoutExpired("powershell", timeout)
        return (None, self._stderr)

    def kill(self):
        self.killed = True


class TestToastOutcomeIsObserved:
    """Toast failures after PowerShell launched (script error, bad exit code)
    used to disappear: the child's output went to DEVNULL and nothing ever
    read its exit status. The watcher logs them without touching the script."""

    @pytest.fixture()
    def watch(self, notify_env):
        logged = []
        notify_env["_log_error"] = logged.append
        notify_env["_logged"] = logged
        return notify_env

    def test_clean_exit_logs_nothing(self, watch):
        watch["_watch_toast"](_FakeProc(0, ""))
        assert watch["_logged"] == []

    def test_non_zero_exit_is_logged_with_its_error_text(self, watch):
        watch["_watch_toast"](_FakeProc(1, "Exception calling LoadXml: bad xml"))
        assert len(watch["_logged"]) == 1
        assert "exited 1" in watch["_logged"][0]
        assert "bad xml" in watch["_logged"][0]

    def test_error_text_with_a_zero_exit_is_still_logged(self, watch):
        # PowerShell non-terminating errors print to stderr and still exit 0.
        watch["_watch_toast"](_FakeProc(0, "some non-terminating error"))
        assert len(watch["_logged"]) == 1
        assert "some non-terminating error" in watch["_logged"][0]

    def test_whitespace_only_stderr_is_not_an_error(self, watch):
        watch["_watch_toast"](_FakeProc(0, "   "))
        assert watch["_logged"] == []

    def test_error_text_is_truncated(self, watch):
        watch["_watch_toast"](_FakeProc(1, "x" * 5000))
        assert len(watch["_logged"][0]) < 400

    def test_a_hung_script_is_stopped_and_logged(self, watch):
        proc = _FakeProc(0, "", hang_first=True)
        watch["_watch_toast"](proc)
        assert proc.killed is True
        assert any("did not finish" in m for m in watch["_logged"])

    def test_no_process_is_a_noop(self, watch):
        watch["_watch_toast"](None)          # launch failed earlier and was already logged
        assert watch["_logged"] == []

    def test_notify_logs_a_failed_toast_end_to_end(self, notify_env):
        logged = []
        notify_env["_log_error"] = logged.append
        notify_env["_run_toast_script"] = lambda script: _FakeProc(1, "toast blew up")
        notify_env["notify"]("Laundry", "25:00")
        assert len(logged) == 1
        assert "toast blew up" in logged[0]

    def test_notify_stays_quiet_when_the_toast_succeeds(self, notify_env):
        logged = []
        notify_env["_log_error"] = logged.append
        notify_env["_run_toast_script"] = lambda script: _FakeProc(0, "")
        notify_env["notify"]("Laundry", "25:00")
        assert logged == []

    def test_launch_pipes_stderr_only_and_returns_the_process(self, monkeypatch):
        seen = {}

        class _FakeSI:
            dwFlags = 0

        sentinel = object()
        def fake_popen(cmd, **kw):
            seen.update(kw)
            return sentinel

        monkeypatch.setattr(subprocess, "STARTUPINFO", _FakeSI, raising=False)
        monkeypatch.setattr(subprocess, "STARTF_USESHOWWINDOW", 1, raising=False)
        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        match = re.search(r"(^def _run_toast_script\(script\):.*?)(?=^def _system_windows_dir)",
                          SOURCE, re.DOTALL | re.MULTILINE)
        ns = {"os": os, "subprocess": subprocess}
        exec(match.group(1), ns)
        ns["_system_windows_dir"] = lambda: r"C:\Windows"
        assert ns["_run_toast_script"]("echo hi") is sentinel
        assert seen["stderr"] == subprocess.PIPE
        assert seen["stdout"] == subprocess.DEVNULL
        assert seen["stdin"] == subprocess.DEVNULL


@pytest.mark.skipif(sys.platform != "win32", reason="runs real PowerShell")
class TestToastOutcomeRealPowerShell:
    """No fakes: the real launch and the real watcher against real PowerShell."""

    @pytest.fixture()
    def real(self):
        match = re.search(
            r"(^_TOAST_WAIT_SECONDS = .*?)(?=^def notify)",
            SOURCE, re.DOTALL | re.MULTILINE,
        )
        assert match, "toast watcher not found in source"
        logged = []
        ns = {"__builtins__": __builtins__, "os": os, "subprocess": subprocess,
              "_log_error": logged.append}
        # _toast_safe and friends are not needed here; only the launch + watcher
        src = re.search(r"(^def _run_toast_script\(script\):.*?)(?=^def _system_windows_dir)",
                        SOURCE, re.DOTALL | re.MULTILINE).group(1)
        sysdir = re.search(r"(^def _system_windows_dir\(\):.*?)(?=^def notify)",
                           SOURCE, re.DOTALL | re.MULTILINE).group(1)
        exec(match.group(1), ns)
        exec(src, ns)
        exec(sysdir, ns)
        ns["_logged"] = logged
        return ns

    def test_a_script_that_throws_is_logged(self, real):
        proc = real["_run_toast_script"]("throw 'toast-script-boom'")
        real["_watch_toast"](proc)
        assert len(real["_logged"]) == 1
        assert "toast-script-boom" in real["_logged"][0]

    def test_a_script_that_succeeds_logs_nothing(self, real):
        proc = real["_run_toast_script"]("$x = 1")
        real["_watch_toast"](proc)
        assert real["_logged"] == []


class TestLogTkException:
    """A3: Tk callback exceptions are routed to _log_error on a single line."""

    @pytest.fixture()
    def handler(self):
        import traceback
        logged = []
        match = re.search(r"(^def _log_tk_exception\(.*?)(?=^def _backup_corrupt_settings)",
                          SOURCE, re.DOTALL | re.MULTILINE)
        assert match, "_log_tk_exception not found in source"
        ns = {"traceback": traceback, "_log_error": logged.append}
        exec(match.group(1), ns)
        return ns["_log_tk_exception"], logged

    def test_exception_is_logged_on_one_line_with_type_and_message(self, handler):
        fn, logged = handler
        try:
            raise ValueError("bad countdown")
        except ValueError:
            fn(*sys.exc_info())
        assert len(logged) == 1
        line = logged[0]
        assert line.startswith("Tk callback exception:")
        assert "ValueError: bad countdown" in line
        assert "\n" not in line

    def test_handler_is_installed_on_the_root_window(self):
        # The window is created at import time, so it can't be exercised
        # headless; this only guards against the wiring line being deleted.
        assert "root.report_callback_exception = _log_tk_exception" in SOURCE


class TestLogErrorFlattensNewlines:
    """A10 (CWE-117): a multi-line message must not fake extra error.log lines."""

    def test_multiline_message_is_one_log_line(self, tmp_path, monkeypatch):
        match = re.search(r"(^def _log_error\(message\):.*?)(?=^def _clip_title)",
                          SOURCE, re.DOTALL | re.MULTILINE)
        ns = {"os": os, "sys": sys, "threading": threading, "_SETTINGS_PATH": str(tmp_path / "settings.json"), "_MAX_LOG_BYTES": 10**6}
        exec(match.group(1), ns)
        ns["_log_error"]("real line\n2026-01-01T00:00:00 FAKE entry\r\nthird")
        lines = (tmp_path / "error.log").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        assert "FAKE entry" in lines[0] and "real line" in lines[0]

    def test_single_line_message_unchanged(self, tmp_path):
        match = re.search(r"(^def _log_error\(message\):.*?)(?=^def _clip_title)",
                          SOURCE, re.DOTALL | re.MULTILINE)
        ns = {"os": os, "sys": sys, "threading": threading, "_SETTINGS_PATH": str(tmp_path / "settings.json"), "_MAX_LOG_BYTES": 10**6}
        exec(match.group(1), ns)
        ns["_log_error"]("plain")
        assert (tmp_path / "error.log").read_text(encoding="utf-8").strip().endswith("plain")


class TestLogErrorHardening:
    """L4: control characters are dropped from log lines, and writers take
    turns so rotation and append cannot race between worker threads."""

    @pytest.fixture()
    def log(self, tmp_path):
        match = re.search(r"(^def _log_error\(message\):.*?)(?=^def _clip_title)",
                          SOURCE, re.DOTALL | re.MULTILINE)
        ns = {"os": os, "sys": sys, "threading": threading,
              "_SETTINGS_PATH": str(tmp_path / "settings.json"), "_MAX_LOG_BYTES": 10**6}
        exec(match.group(1), ns)
        ns["_path"] = tmp_path / "error.log"
        return ns

    def test_ansi_escapes_and_other_controls_are_dropped(self, log):
        esc = chr(27)
        log["_log_error"]("before" + esc + "[31mRED" + esc + "[0m" + chr(0) + chr(7) + chr(8) + chr(127) + "after")
        line = log["_path"].read_text(encoding="utf-8")
        for bad in (esc, chr(0), chr(7), chr(8), chr(127)):
            assert bad not in line
        assert "before[31mRED[0mafter" in line

    def test_tab_and_normal_unicode_survive(self, log):
        text = "caf" + chr(233) + " " + chr(0x2713) + " a" + chr(9) + "b"
        log["_log_error"](text)
        assert text in log["_path"].read_text(encoding="utf-8")

    def test_still_one_line_per_message(self, log):
        log["_log_error"]("one" + chr(27) + chr(10) + "two")
        assert len(log["_path"].read_text(encoding="utf-8").splitlines()) == 1

    def test_writers_never_overlap_inside_the_append(self, log):
        # A fake open that dawdles while recording how many writers are inside
        # it at once. Without the lock several threads are inside together.
        inside = {"now": 0, "max": 0}
        guard = threading.Lock()
        real_open = open

        class _SlowFile:
            def __init__(self, f):
                self._f = f
            def __enter__(self):
                with guard:
                    inside["now"] += 1
                    inside["max"] = max(inside["max"], inside["now"])
                time.sleep(0.01)
                return self._f.__enter__()
            def __exit__(self, *a):
                with guard:
                    inside["now"] -= 1
                return self._f.__exit__(*a)

        log["open"] = lambda *a, **k: _SlowFile(real_open(*a, **k))
        threads = [threading.Thread(target=log["_log_error"], args=("msg " + str(i),)) for i in range(8)]
        for t_ in threads:
            t_.start()
        for t_ in threads:
            t_.join(10)
        assert inside["max"] == 1
        assert len(log["_path"].read_text(encoding="utf-8").splitlines()) == 8


# ---------------------------------------------------------------------------
# B2: the tests above only inspect the generated script text. These run it in
# real PowerShell, where the actual injection (a title's "$(...)" being
# expanded inside winotify's @"..."@ here-string) would happen.
# ---------------------------------------------------------------------------

def _powershell_exe():
    # Same lookup as the app (the OS's own answer, not the SystemRoot env var), so
    # these injection tests run against the PowerShell the app really launches.
    import ctypes
    buf = ctypes.create_unicode_buffer(260)
    n = ctypes.windll.kernel32.GetSystemWindowsDirectoryW(buf, 260) if sys.platform == "win32" else 0
    root = buf.value if 0 < n < 260 else r"C:\Windows"
    return os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")


_real_powershell = pytest.mark.skipif(
    sys.platform != "win32" or not os.path.exists(_powershell_exe()),
    reason="needs real Windows PowerShell",
)


def _run_powershell(script, cwd):
    return subprocess.run(
        [_powershell_exe(), "-NoProfile", "-Command", script],
        cwd=cwd, capture_output=True, text=True, timeout=60,
    )


@_real_powershell
class TestRealPowerShellInjection:
    MARKER = "pwned.txt"  # relative: PowerShell runs with cwd=tmp_path, titles cap at 50 chars

    def test_control_detector_sees_a_vulnerable_script_execute(self, tmp_path):
        """Proves this harness can fail: a here-string that embeds the payload
        literally (the original bug) DOES create the marker."""
        script = f'$Template = @"\n<text>$(New-Item {self.MARKER})</text>\n"@\n$Template'
        _run_powershell(script, tmp_path)
        assert (tmp_path / self.MARKER).exists()

    @pytest.mark.parametrize("payload", [
        "$(New-Item pwned.txt)",
        "Hi $(New-Item pwned.txt) there",  # subexpression buried mid-sentence
        # newline + a line-leading "@ would close the here-string if it were literal
        'a\n"@\nNew-Item pwned.txt\n$x=@"\nb',
    ])
    def test_payload_in_title_never_executes(self, notify_env, tmp_path, payload):
        notify_env["notify"](payload, "5:00")
        script = notify_env["_ps_calls"][0]
        # Everything up to (not including) the toast-showing half: the Base64
        # decode and the real winotify template's @"..."@ here-string, which is
        # where an injected "$(...)" would execute. No toast is shown.
        head, sep, _ = script.partition("$SerializedXml = New-Object")
        assert sep, "toast template layout changed; update this split"

        result = _run_powershell(head + "\n$Template", tmp_path)

        assert not (tmp_path / self.MARKER).exists(), result.stdout + result.stderr
        assert result.returncode == 0, result.stderr
        # The payload came through as inert text in the expanded template.
        assert "New-Item pwned.txt" in result.stdout
