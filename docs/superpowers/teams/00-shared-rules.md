# Diarchy App — Shared rules for every team

You are the orchestrator of ONE team. Main (the integration orchestrator) runs on branch
`feat/diarchy-app` at `$HOME/src/diarchy/.worktrees/feat-diarchy-app`.

## Authority

1. `docs/superpowers/specs/2026-10-11-diarchy-app-design.md` — binding spec and contracts. Read it fully.
2. `CONTEXT.md` — vocabulary. `docs/adr/0001-*`, `docs/adr/0002-*` — decisions you must not reverse.
3. Your team brief — your ownership boundary, deliverables, and stages.

If the spec is silent or contradictory, decide (spec wins over brief), record `Ruling: <what> — <why> — <cost if
wrong>` in your report, and continue. Ask Main only when another team's contract must change.

## Process (skills)

1. Read `skill://writing-plans`, then write your plan to
   `docs/superpowers/plans/2026-10-11-diarchy-app-<team>.md` (inside your worktree) covering ONLY your
   ownership. Self-review it against the spec sections listed in your brief.
2. Send Main a 5-line plan summary (see Messaging). Do not wait for a reply; continue.
3. Execute with `skill://subagent-driven-development` (fresh implementer per task + task review), using
   `skill://tdd` discipline: failing test first, then code. Subagents MUST be launched with model
   `anthropic/claude-sonnet-5-5` and high thinking. Implementers never spawn their own reviewers.
4. Before touching an external tool's CLI/API you have not verified in this repo (Moonlight CLI, Sunshine API,
   SwiftUI MenuBarExtra APIs, Swift Testing), dispatch a docs-research agent first.
5. Finish with `skill://verification-before-completion`: run your owned test files and paste real output into the
   report.

## Boundaries

- Edit ONLY files in your ownership list. Need a change elsewhere? Message Main with the exact change; never edit it.
- Never run `sudo`, never contact the real Host (`ssh`, `diarchy-ssh`) — it is unreachable and tests must use fakes.
- Never push, never merge into `feat/diarchy-app`, never rebase. Commit on your own branch with explicit paths.
- Commit early and often (subagents have a ~30 min runtime cap).
- Tests: Python `unittest` in `tests/` with fake executables on `PATH` and a temp `HOME` (copy the style of
  `tests/test_route_selection.py`). Run only your owned test files plus any you add:
  `python3 -m unittest tests.test_x tests.test_y -v`. The full suite takes ~2 min; run it once at the end.
- No new third-party dependencies (Python stdlib only on host; no SwiftPM dependencies).
- User-facing text in the app is English; code, comments, logs, and commit messages are English.
- No hardcoded personal values (IPs `192.0.2.10`, `100.64.0.10`, `192.0.2.20`, user `user`, key
  paths) may remain in product code. Test fixtures may use RFC 5737 addresses (`192.0.2.x`, `198.51.100.x`) and
  `100.64.0.x` for Tailscale.

## Messaging

One line per message, prefixed with your team id:

```
send to the orchestrator: "[<team>→Main] <message>"
```

Send: the plan summary, each `READY <token> <commit hash>` your brief asks for, blockers that need another team,
and `DONE <final commit hash> report=<path>`. Main replies by typing into your pane as `[Main→<team>] ...`.
When Main broadcasts `MERGE <token> <hash>`, run `git merge --no-edit <hash>` on your branch before continuing.

## Final report

Write `docs/superpowers/reports/2026-10-11-<team>.md` (commit it) with: deliverables vs. brief checklist, files
changed, test commands and real output, every `Ruling:`, deferred minors, and anything only physical verification
on a real Host can prove.
