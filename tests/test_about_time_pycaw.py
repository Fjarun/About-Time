"""
Tests for the pycaw-backed volume functions (_find_own_audio_session,
_get_app_volume, _set_app_volume) — specifically the crash-safety fix where
a device unplug/switch mid-call used to throw a raw COMError out of a Tk
callback (about_time.py's audio session helpers).

AudioUtilities.GetAllSessions() is faked here rather than exercised against
real hardware — that lets every path (session found/missing, property
access raising) be tested deterministically regardless of what's actually
plugged in on the machine running the suite.
"""

import os
import re
import sys
import textwrap
import threading
import types
from pathlib import Path

import pytest

SRC_PATH = Path(__file__).parent.parent / "about_time.py"
SOURCE = SRC_PATH.read_text(encoding="utf-8")


class FakeVolume:
    def __init__(self, level=0.5, raise_on_get=False, raise_on_set=False):
        self.level = level
        self.raise_on_get = raise_on_get
        self.raise_on_set = raise_on_set
        self.set_calls = []

    def GetMasterVolume(self):
        if self.raise_on_get:
            raise OSError("simulated device unplug")
        return self.level

    def SetMasterVolume(self, level, guid):
        if self.raise_on_set:
            raise OSError("simulated device unplug")
        self.set_calls.append(level)
        self.level = level


class FakeSession:
    def __init__(self, pid, volume=None):
        self.Process = types.SimpleNamespace(pid=pid) if pid is not None else None
        self.SimpleAudioVolume = volume


class FakeAudioUtilities:
    def __init__(self, sessions=None, raise_on_enumerate=False):
        self._sessions = sessions or []
        self.raise_on_enumerate = raise_on_enumerate

    def GetAllSessions(self):
        if self.raise_on_enumerate:
            raise OSError("simulated enumeration failure")
        return self._sessions


@pytest.fixture()
def pycaw_env():
    match = re.search(
        r"(^    def _find_own_audio_session\(\):.*?)(?=^    def _prime_audio_session)",
        SOURCE, re.DOTALL | re.MULTILINE,
    )
    assert match, "pycaw volume functions not found in source"
    src = textwrap.dedent(match.group(1))

    audio_utilities = FakeAudioUtilities()
    ns = {
        "__builtins__": __builtins__,
        "os": os,
        "sys": sys,
        "_log_error": lambda message: None,
        "AudioUtilities": audio_utilities,
    }
    exec(src, ns)
    ns["_audio_utilities"] = audio_utilities
    return ns


def _set_sessions(env, sessions):
    env["_audio_utilities"]._sessions = sessions


class TestFindOwnAudioSession:
    def test_finds_session_matching_own_pid(self, pycaw_env):
        own_pid = os.getpid()
        target = FakeSession(pid=own_pid)
        _set_sessions(pycaw_env, [FakeSession(pid=own_pid + 1), target])

        assert pycaw_env["_find_own_audio_session"]() is target

    def test_returns_none_when_no_session_matches(self, pycaw_env):
        _set_sessions(pycaw_env, [FakeSession(pid=os.getpid() + 1)])

        assert pycaw_env["_find_own_audio_session"]() is None

    def test_returns_none_when_no_sessions_exist_yet(self, pycaw_env):
        _set_sessions(pycaw_env, [])

        assert pycaw_env["_find_own_audio_session"]() is None

    def test_session_with_no_process_is_skipped_not_raised(self, pycaw_env):
        # A session can have Process=None (e.g. a system sound that has
        # since ended) — must be skipped, not crash on .pid access.
        own_pid = os.getpid()
        target = FakeSession(pid=own_pid)
        _set_sessions(pycaw_env, [FakeSession(pid=None), target])

        assert pycaw_env["_find_own_audio_session"]() is target

    def test_enumeration_failure_returns_none_not_raise(self, pycaw_env):
        pycaw_env["_audio_utilities"].raise_on_enumerate = True

        assert pycaw_env["_find_own_audio_session"]() is None


class TestGetAppVolume:
    def test_returns_level_when_session_found(self, pycaw_env):
        vol = FakeVolume(level=0.73)
        _set_sessions(pycaw_env, [FakeSession(pid=os.getpid(), volume=vol)])

        assert pycaw_env["_get_app_volume"]() == 0.73

    def test_returns_none_when_no_session(self, pycaw_env):
        _set_sessions(pycaw_env, [])

        assert pycaw_env["_get_app_volume"]() is None

    def test_returns_none_when_simple_audio_volume_is_none(self, pycaw_env):
        _set_sessions(pycaw_env, [FakeSession(pid=os.getpid(), volume=None)])

        assert pycaw_env["_get_app_volume"]() is None

    def test_device_error_on_get_returns_none_not_raise(self, pycaw_env):
        # The actual crash-safety fix: GetMasterVolume() throwing (e.g. the
        # audio device was unplugged between finding the session and
        # reading it) must be caught, not propagate out of a Tk callback.
        vol = FakeVolume(raise_on_get=True)
        _set_sessions(pycaw_env, [FakeSession(pid=os.getpid(), volume=vol)])

        assert pycaw_env["_get_app_volume"]() is None


class TestSetAppVolume:
    def test_sets_level_when_session_found(self, pycaw_env):
        vol = FakeVolume(level=0.2)
        _set_sessions(pycaw_env, [FakeSession(pid=os.getpid(), volume=vol)])

        pycaw_env["_set_app_volume"](0.9)

        assert vol.set_calls == [0.9]

    def test_clamps_above_one(self, pycaw_env):
        vol = FakeVolume()
        _set_sessions(pycaw_env, [FakeSession(pid=os.getpid(), volume=vol)])

        pycaw_env["_set_app_volume"](1.5)

        assert vol.set_calls == [1.0]

    def test_clamps_below_zero(self, pycaw_env):
        vol = FakeVolume()
        _set_sessions(pycaw_env, [FakeSession(pid=os.getpid(), volume=vol)])

        pycaw_env["_set_app_volume"](-0.5)

        assert vol.set_calls == [0.0]

    def test_no_session_is_a_noop_not_raise(self, pycaw_env):
        _set_sessions(pycaw_env, [])

        pycaw_env["_set_app_volume"](0.5)  # must not raise

    def test_device_error_on_set_does_not_raise(self, pycaw_env):
        vol = FakeVolume(raise_on_set=True)
        _set_sessions(pycaw_env, [FakeSession(pid=os.getpid(), volume=vol)])

        pycaw_env["_set_app_volume"](0.5)  # must not raise


@pytest.fixture()
def prime_audio_env():
    """Exercises _prime_audio_session in isolation — _wrap_wav/_apply_reverb/
    _sine_segment and winsound.PlaySound are stubbed since only the
    first-boot-volume side effect (not real sound synthesis) is under test."""
    match = re.search(
        r"(^    def _find_own_audio_session\(\):.*?)(?=^else:)",
        SOURCE, re.DOTALL | re.MULTILINE,
    )
    assert match, "audio session functions not found in source"
    src = textwrap.dedent(match.group(1))

    audio_utilities = FakeAudioUtilities()
    fake_winsound = types.SimpleNamespace(
        PlaySound=lambda *a, **k: None, SND_MEMORY=0,
    )
    com_calls = []   # (event, thread ident), in the order they happened
    fake_comtypes = types.SimpleNamespace(
        CoInitialize=lambda: com_calls.append(("init", threading.get_ident())),
        CoUninitialize=lambda: com_calls.append(("uninit", threading.get_ident())),
    )
    logged = []
    ns = {
        "__builtins__": __builtins__,
        "os": os,
        "sys": sys,
        "_log_error": logged.append,
        "comtypes": fake_comtypes,
        "threading": __import__("threading"),
        "AudioUtilities": audio_utilities,
        "winsound": fake_winsound,
        "_wrap_wav": lambda w: w,
        "_apply_reverb": lambda w: w,
        "_sine_segment": lambda *a, **k: b"",
    }
    exec(src, ns)
    ns["_audio_utilities"] = audio_utilities
    ns["_com_calls"] = com_calls
    ns["_logged"] = logged
    return ns


class TestPrimeAudioSession:
    def test_sets_first_boot_volume_after_priming(self, prime_audio_env):
        vol = FakeVolume(level=1.0)
        _set_sessions(prime_audio_env, [FakeSession(pid=os.getpid(), volume=vol)])

        prime_audio_env["_prime_audio_session"](0.5)
        for t in prime_audio_env["threading"].enumerate():
            if t is not prime_audio_env["threading"].main_thread():
                t.join(timeout=2)

        assert vol.set_calls == [0.5]

    def test_no_first_boot_volume_leaves_volume_untouched(self, prime_audio_env):
        vol = FakeVolume(level=1.0)
        _set_sessions(prime_audio_env, [FakeSession(pid=os.getpid(), volume=vol)])

        prime_audio_env["_prime_audio_session"]()
        for t in prime_audio_env["threading"].enumerate():
            if t is not prime_audio_env["threading"].main_thread():
                t.join(timeout=2)

        assert vol.set_calls == []

    def test_com_initialised_on_the_worker_thread_around_the_volume_set(self, prime_audio_env):
        # COM must be initialised once per thread. The volume is set from a
        # worker thread, so without CoInitialize there every pycaw call fails
        # and the first-boot cap silently never applies.
        env = prime_audio_env
        vol = FakeVolume(level=1.0)
        _set_sessions(env, [FakeSession(pid=os.getpid(), volume=vol)])
        set_thread = []
        orig_set = vol.SetMasterVolume
        vol.SetMasterVolume = lambda level, guid: (set_thread.append(threading.get_ident()),
                                                   orig_set(level, guid))

        env["_prime_audio_session"](0.5)
        _join_workers()

        events = [e for e, _ in env["_com_calls"]]
        assert events == ["init", "uninit"]
        worker = {tid for _, tid in env["_com_calls"]}
        assert len(worker) == 1 and threading.get_ident() not in worker   # a worker, not main
        assert set_thread == list(worker)                                 # set ran on that thread
        assert vol.set_calls == [0.5]

    def test_com_is_released_even_if_the_volume_set_raises(self, prime_audio_env):
        env = prime_audio_env
        _set_sessions(env, [FakeSession(pid=os.getpid(), volume=FakeVolume(raise_on_set=True))])

        env["_prime_audio_session"](0.5)
        _join_workers()

        assert [e for e, _ in env["_com_calls"]] == ["init", "uninit"]

    def test_com_is_not_touched_when_no_first_boot_volume(self, prime_audio_env):
        env = prime_audio_env
        _set_sessions(env, [FakeSession(pid=os.getpid(), volume=FakeVolume())])

        env["_prime_audio_session"]()
        _join_workers()

        assert env["_com_calls"] == []

    def test_com_init_failure_is_logged_and_volume_left_alone(self, prime_audio_env):
        env = prime_audio_env
        vol = FakeVolume(level=1.0)
        _set_sessions(env, [FakeSession(pid=os.getpid(), volume=vol)])
        def boom():
            raise OSError("COM unavailable")
        env["comtypes"].CoInitialize = boom

        env["_prime_audio_session"](0.5)
        _join_workers()

        assert vol.set_calls == []
        assert any("could not initialise COM" in m for m in env["_logged"])
        assert env["_com_calls"] == []                  # never uninitialises what it never initialised


def _join_workers():
    for t in threading.enumerate():
        if t is not threading.main_thread():
            t.join(timeout=5)


class TestPlayThreadFailuresAreLogged:
    """_play and the priming sound run on bare worker threads, where an
    uncaught exception only reaches stderr (invisible in the windowed exe)."""

    @pytest.fixture()
    def play_env(self):
        match = re.search(
            r"(^    def _play\(wav\):.*?)(?=^    def _find_own_audio_session)",
            SOURCE, re.DOTALL | re.MULTILINE,
        )
        assert match, "_play not found in source"
        logged = []
        def boom(*a, **k):
            raise RuntimeError("Failed to play sound")
        ns = {
            "__builtins__": __builtins__,
            "winsound": types.SimpleNamespace(PlaySound=boom, SND_MEMORY=0),
            "_log_error": logged.append,
        }
        exec(textwrap.dedent(match.group(1)), ns)
        ns["_logged"] = logged
        return ns

    def test_play_failure_is_logged_not_raised(self, play_env):
        play_env["_play"](b"wav")            # must not raise
        assert any("_play failed" in m and "Failed to play sound" in m
                   for m in play_env["_logged"])

    def test_play_failure_inside_a_real_thread_reaches_the_log(self, play_env):
        t = threading.Thread(target=play_env["_play"], args=(b"wav",))
        t.start()
        t.join(timeout=5)
        assert any("_play failed" in m for m in play_env["_logged"])

    def test_priming_sound_failure_is_logged_and_cap_still_attempted(self, prime_audio_env):
        env = prime_audio_env
        def boom(*a, **k):
            raise RuntimeError("no audio device")
        env["winsound"].PlaySound = boom
        vol = FakeVolume(level=1.0)
        _set_sessions(env, [FakeSession(pid=os.getpid(), volume=vol)])

        env["_prime_audio_session"](0.5)
        _join_workers()

        assert any("could not play the priming sound" in m for m in env["_logged"])
        assert vol.set_calls == [0.5]        # a missing device must not skip the cap


_CHILD = """
import json, os, struct, sys, threading, winsound
import comtypes
from pycaw.pycaw import AudioUtilities

src = open(sys.argv[1], encoding="utf-8").read()
raw = struct.pack("<h", 1) * 2205
wav = struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(raw), b"WAVE",
                  b"fmt ", 16, 1, 1, 44100, 88200, 2, 16, b"data", len(raw)) + raw
logged = []
ns = {"__builtins__": __builtins__, "os": os, "sys": sys, "threading": threading,
      "_log_error": logged.append, "AudioUtilities": AudioUtilities,
      "winsound": winsound, "comtypes": comtypes,
      "_wrap_wav": lambda w: wav, "_apply_reverb": lambda w: w,
      "_sine_segment": lambda *a, **k: b""}
exec(src, ns)
before = threading.enumerate()
ns["_prime_audio_session"](0.37)          # the worker thread is the first COM user
for t in threading.enumerate():
    if t not in before:
        t.join(10)
level = ns["_get_app_volume"]()
ns["_set_app_volume"](1.0)                # Windows remembers per-program volume
print(json.dumps({"level": level, "logged": logged}))
"""


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows audio session needed")
class TestPrimeAudioSessionRealThread:
    """No fakes for audio or COM: the real winsound, pycaw and comtypes, with
    the worker thread as the first and only COM user, in a fresh process. The
    mocked tests above can only show the right calls are made. This one fails
    against the original code, where the thread had no COM and the cap was
    silently dropped. A subprocess is required: once any thread in a process
    has initialised COM, later threads behave differently, and Windows also
    remembers a program's volume between runs, so only a clean process and the
    error log (not the final level alone) tell the two apart."""

    def test_first_boot_volume_is_applied_without_errors(self, tmp_path):
        import json
        import subprocess

        match = re.search(
            r"(^    def _find_own_audio_session\(\):.*?)(?=^else:)",
            SOURCE, re.DOTALL | re.MULTILINE,
        )
        assert match, "audio session functions not found in source"
        slice_file = tmp_path / "audio_slice.py"
        slice_file.write_text(textwrap.dedent(match.group(1)), encoding="utf-8")

        done = subprocess.run(
            [sys.executable, "-c", _CHILD, str(slice_file)],
            capture_output=True, text=True, timeout=60,
        )
        assert done.returncode == 0, done.stderr
        result = json.loads(done.stdout.strip().splitlines()[-1])

        assert result["logged"] == [], result["logged"]
        if result["level"] is None:
            pytest.skip("no audio endpoint on this machine, so no session to inspect")
        assert result["level"] == pytest.approx(0.37, abs=0.02)
