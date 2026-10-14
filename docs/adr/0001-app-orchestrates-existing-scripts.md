# 0001 — The Mac app orchestrates the existing scripts instead of re-implementing them

**Status:** Accepted (2026-10-11)

## Context

Diarchy already ships hardened bash and Python scripts for the KVM lifecycle (`diarchy-kvm`, with locks,
rollback, and process identity checks), routing (`diarchy-route.sh`), clipboard and file portals, installers with
pinned checksums, and doctors — covered by 245 behavior tests and physically verified. The new requirement is a GUI
that any user can configure.

## Decision

Build a native SwiftUI menu-bar app that owns the single configuration file (`diarchy.conf`) and the user
interface, and invokes the existing scripts (Mac) and a new JSON-speaking agent (Host, over SSH) for every action.
Scripts read their settings from `diarchy.conf` through one strict loader.

## Alternatives considered

- **Rewrite all Mac logic in Swift:** one language, but re-implements and re-verifies process lifecycle that
  already works, with high regression risk for clipboard and file portals.
- **Web/Electron/Tauri UI:** cross-platform but heavier, contradicting the "light on the Mac" requirement, and the
  Controller is Mac-only.

## Consequences

- The app depends on a stable textual contract with the scripts (spec §5) and a JSON contract with the Agent
  (spec §6). Changing either is a cross-team change.
- Individual pieces can migrate to Swift later behind the same contracts.
