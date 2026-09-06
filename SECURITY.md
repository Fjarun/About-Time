# Security Policy

About Time is a small, solo-maintained desktop app. This document covers what's actually in scope for a security report and how to send one privately.

## Scope

About Time is a local Windows desktop application. It:

- Stores all settings locally, in `%APPDATA%\About Time\settings.json` — no cloud sync, no account, no server component.
- Makes no network calls. It doesn't phone home, check for updates automatically, or send any data anywhere.
- Synthesises its own notification sounds in-memory (no bundled audio files, no external asset downloads).

Given that, most "security" concerns for a project like this reduce to: does a malicious `settings.json`, a malicious build artifact, or a supply-chain issue in a dependency create risk. Those are the things worth reporting privately below. General bugs, crashes, or feature requests that aren't security issues should go through the normal [GitHub Issues](https://github.com/Fjarun/About-Time/issues) instead.

## Supported Versions

Only the latest released version is supported. This is an actively developed solo project — please update to the newest release before reporting an issue, older versions won't receive fixes.

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Use this repo's private vulnerability reporting instead: go to the [Security tab](https://github.com/Fjarun/About-Time/security) and click **Report a vulnerability**. This opens a private draft advisory visible only to the maintainer — nothing is public until a fix is ready. I'll aim to acknowledge within a few days — this is a solo-maintained project, response time may vary, but reports are taken seriously.

Please include:
- A description of the issue and its potential impact
- Steps to reproduce, if applicable
- Which version of About Time is affected

## Disclosure

I'll credit reporters (if desired) once a fix is released, and appreciate coordinated disclosure — please give a reasonable window to ship a fix before going public.
