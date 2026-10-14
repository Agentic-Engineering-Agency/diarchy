# 0002 — Privileged host changes are shown as required actions, never executed

**Status:** Accepted (2026-10-11)

## Context

Several Host prerequisites need root: installing packages, firewall rules for UDP 4242, the `uinput` udev rule for
streaming input, and the lid-switch policy. The app must work for any user without weakening the Host.

## Decision

Neither the app, the scripts, nor the Agent run `sudo` or handle a sudo password. The Agent detects each missing
prerequisite and returns a *required action* with the exact command; the app displays it with a copy button and
re-checks after the user runs it.

## Alternatives considered

- **App asks for the sudo password and runs commands over SSH:** fully automatic, but the app would handle a
  privileged secret.
- **Limited sudoers rule:** automatic after one setup, but permanently widens privileges on the Host.

## Consequences

- First-time setup has a few copy-and-run steps on the Host.
- The Agent can only detect, not guarantee, some conditions (for example, it cannot read UFW rules as a normal
  user), so firewall actions are `recommended`, not `required`.
