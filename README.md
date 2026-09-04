# About Time - Always-on-Top Timer App

![Version](https://img.shields.io/badge/version-v0.9.0-blue)
![Language](https://img.shields.io/badge/language-Python-3776AB?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/platform-Windows-0078D4?logo=windows&logoColor=white)
![Status](https://img.shields.io/badge/status-Active%20Development-orange)
![License](https://img.shields.io/badge/license-MIT-green)
![Downloads](https://img.shields.io/github/downloads/Fjarun/About-Time/total)

> **This project is in active development.** Features and behaviour may change between releases.
>
> MIT licensed — free to use, copy, modify, and redistribute, including commercially, as long as the license text stays attached.

**A free Windows desktop timer that stays on top of everything else.**

About Time keeps your countdowns always visible, no matter what you're working on. Run up to 5 named task timers simultaneously, each with its own label and countdown, and choose from an optional family of notification sounds when each one finishes. Perfect for productivity workflows where you need a floating timer that doesn't disappear behind your browser or IDE.

1. [Why This Exists](#why-this-exists)
1. [Download](#download)
1. [Features](#features)
1. [Tech Stack](#tech-stack)

## Why This Exists

Every always-on-top timer solution I found was either bundled into something heavier than I wanted (a full productivity suite, a Pomodoro app with its own opinionated workflow) or was a single fixed timer with no room for running several named countdowns side by side. I wanted something narrower: a small floating window, several independent timers I could label myself, sane persistence so a timer I'm mid-way through survives a restart, and nothing else competing for attention.

About Time is that — built to fit a personal need first, and shared because a small focused tool that does one thing well tends to be useful to more people than just the one who built it. It's also an ongoing practical exercise in working with Claude Code as an AI-assisted development tool, developed in the open as both a working app and a real example of that workflow.

## Download

Grab the latest `.exe` from [Releases](https://github.com/Fjarun/About-Time/releases) — no install required, runs directly on Windows.

No Python or code needed.

## Features

<img src="Assets/Demo.gif" alt="About Time demo — adding, naming and running timers" align="right" width="300">

- **Always-on-top toggle** — pin the window so it floats above everything else on screen
- Run up to **5 simultaneous countdown timers** at once, each independently named and tracked
- Add timers one at a time via the **Add timer** button; remove any individual slot with its **x** button — no need to manage a fixed set
- Custom time input per timer (maximum 99:59:59), with flexible input: plain numbers assumed as minutes, or suffix with `s`, `m`, or `h`
- Click the countdown display to edit the time at any point — even mid-run
- **Windows desktop notifications** — opt-in toast alerts when any timer finishes, showing the timer's name or duration if untitled

### Sounds

- Three notification chimes — short, medium, and long — designed as a matched family with consistent tone and feel
- **Mute option** — silence all timer alerts for distraction-free or shared-space use
- **Volume control** — adjust in 5% increments directly from the main window
- Sound selector always visible with instant preview on click, so you always know what you're setting

### Persistence

- Window position, timer count, timer names and durations are all remembered between sessions
- Mid-run and paused timers restore at their remaining time on reopen — ready to resume or reset, no progress lost on accidental close
- Sound choice, volume, always-on-top state, and notification preference saved automatically

## Tech Stack

| | |
|---|---|
| Language | Python 3.x |
| UI | customtkinter |
| Audio | winsound + struct (WAV synthesised in-memory — no audio files) |
| Notifications | winotify (Windows toast) |
| Build | PyInstaller |
