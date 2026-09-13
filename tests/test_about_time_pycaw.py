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
    ns = {
        "__builtins__": __builtins__,
        "os": os,
        "sys": sys,
        "threading": __import__("threading"),
        "AudioUtilities": audio_utilities,
        "winsound": fake_winsound,
        "_wrap_wav": lambda w: w,
        "_apply_reverb": lambda w: w,
        "_sine_segment": lambda *a, **k: b"",
    }
    exec(src, ns)
    ns["_audio_utilities"] = audio_utilities
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
