# About Time

## Summary
A free Windows desktop always-on-top countdown timer app. Lets the user run up to 5 named timers simultaneously with notification sounds and Windows toast alerts. Built as a personal productivity tool and a practical exercise in Claude Code.

## Language / stack
- Python 3.x
- customtkinter (UI)
- winsound + struct + math (sound synthesis — WAV built in-memory, no audio files)
- winotify (Windows toast notifications)
- PyInstaller (exe builds via `About Time.spec` — local only, not tracked)

## Key files
- `about_time.py` — entire application, single file
- `About Time.exe` — current release build, tracked on GitHub
- `dist/` — internal versioned builds (e.g. `About Time v0.5.2.exe`), gitignored

## Notes
- Windows only — sound and notification code is platform-gated
- Sounds are synthesised at runtime (sine waves with reverb), not loaded from files
- Three sound lengths: short, medium, long — all from the same matched family
- Volume stored as a float; WAVs are rebuilt when volume changes
- Settings persistence (sound choice, always-on-top, notification toggle) is the next planned feature
- Repo is public on GitHub: https://github.com/Fjarun/About-Time
