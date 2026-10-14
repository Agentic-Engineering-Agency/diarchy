# Contributing to Diarchy

Diarchy is a beta. It was verified on one Mac and one Omarchy laptop. The most useful help is a hardware report.
By taking part you agree to the [Code of Conduct](CODE_OF_CONDUCT.md). Contributions are under the [MIT license](LICENSE).

## 1. Send a hardware report

A hardware report tells us what works on your Mac and your Omarchy host. Positive reports are as useful as bug reports.
It takes about 3 minutes.

1. Open Diarchy on the Mac. Open Settings.
2. Click "Copy Compatibility Report". The text contains these fields in this order: `Diarchy version`,
   `Mac model identifier`, `Chip`, `macOS version`, `Moonlight version`, `Lan Mouse version`, `Tailscale installed`,
   `Route used (lan/tailscale/none)`, and the host report as a `json` block.
3. Read the text. Remove any IP address, name or host name that is still in it.
4. Open a [hardware report](https://github.com/Agentic-Engineering-Agency/diarchy/issues/new?template=hardware-report.yml),
   paste the text, and fill in what worked.

The compatibility matrix is in [docs/compat/matrix.yml](docs/compat/matrix.yml). The fields are in
[docs/compat/README.md](docs/compat/README.md). A row with `ok` needs a link to a report.

## 2. Report a bug

1. Search the existing issues first.
2. Open a [bug report](https://github.com/Agentic-Engineering-Agency/diarchy/issues/new?template=bug.yml).
3. One machine per report. Say if it is a regression and since which version.
4. Never paste IP addresses, SSIDs, host names, SSH keys, Sunshine credentials or tokens.

## 3. Where not to report

Report a bug in Sunshine, Moonlight, Lan Mouse, Tailscale, Omarchy or Hyprland to that project.
Report it here only if you can show that Diarchy causes it.

For a security problem, follow [SECURITY.md](SECURITY.md). Do not open a public issue.

## 4. Development setup

Requirements: an Apple Silicon Mac with macOS 14 or later, Xcode or the Swift 6 toolchain, and the Apple Command Line Tools (`xcode-select --install`).
The host agent uses only the Python standard library.

| Folder | Content |
|---|---|
| `app/` | The SwiftUI menu-bar app (`DiarchyCore` library and `Diarchy` executable). |
| `client/` | Scripts and installers that run on the Mac. |
| `host/` | The `diarchy_host` Python package, the `diarchy-host` CLI, scripts, installers and systemd user units. |
| `tests/` | Python `unittest` tests for the host agent and the scripts. |
| `docs/` | User docs, the compatibility matrix, ADRs, specs and plans. |

Build the app from source: `app/scripts/build-app.sh`. The result is `app/build/Diarchy.app`.
See [docs/install.md](docs/install.md).

### Tests

Run these from the repository root.

```sh
# Python tests (the full run takes more than 5 minutes)
python3 -m unittest discover -s tests -p 'test_*.py'
# One module while you work
python3 -m unittest discover -s tests -p 'test_display.py'
# Swift tests
swift test --package-path app
# Shell syntax of every script you touch
bash -n client/install.sh
shellcheck client/install.sh   # if installed
```

### Leak gate

The public tree must hold no personal data. Run the gate before each pull request:

```sh
scripts/check-public-tree
```

Exit code 0 means clean. Exit code 1 prints `path:line: <kind>` for each hit. The gate never prints the matched value.
Paths in [.publicignore](.publicignore) are skipped. For examples in code, tests and docs use only documentation values:
`192.0.2.10`, `192.0.2.20`, `198.51.100.7`, `100.64.0.10`, `100.64.0.20`, `example-ssid`, `host.example`,
`00:00:5e:00:53:01`, `$HOME` and `/home/user`.

## 5. Pull requests

1. Open an issue first for a large change.
2. Keep a pull request small and about one thing.
3. Add a test for every change in behavior. A hardware quirk in `host/diarchy_host/gpu.py` needs evidence (a link to a
   report) and a unit test that uses a fake sysfs root. No test, no merge.
4. Do not add a dependency on the host. The host agent is standard-library Python. See
   [ADR 0002](docs/adr/0002-no-sudo-required-actions.md): no step of the installers or the agent can need `sudo`.
   The agent prints the privileged command and the user runs it.
5. Do not copy code from Sunshine, Moonlight, Lan Mouse or Deskflow into this MIT repository.
   Calling them as separate programs is fine.
6. All user-facing text is English: app UI, notifications, script messages and docs.
7. Run the Python tests, the Swift tests and the leak gate. Write the results in the pull request.
8. AI-assisted changes are welcome if you ran them on real hardware and the test output is real.
9. Never advise users to turn off Gatekeeper or to strip the quarantine flag from whole folders. The only quarantine
   command allowed in docs is `xattr -dr com.apple.quarantine /Applications/Diarchy.app`, after the SHA-256 check.

## 6. Release checklist (maintainers)

1. Set the version in `host/VERSION`. Update the changelog or release notes.
2. Run the Python tests, `swift test --package-path app` and `scripts/check-public-tree`.
3. Review every upstream pin and its SHA-256. A wrong pin update would pass the checks in the installers.
   - Lan Mouse: `client/install-lan-mouse.sh` and `host/install-lan-mouse.sh`.
     The Mac installer removes the quarantine flag of the Lan Mouse copy after the SHA-256 check (SEC-23).
   - Sunshine: `host/install.sh`.
   - Moonlight: `client/install.sh`.
4. Check the pins against the official release pages and the GitHub release API. Update
   [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) if a version changes.
5. Update the verified-on row in [docs/compat/matrix.yml](docs/compat/matrix.yml) if hardware was re-tested.
6. Push a tag `vX.Y.Z` (for example `v0.1.0-beta.1`). The `release` workflow builds the ad-hoc signed app,
   writes `SHA256SUMS`, and creates a draft release.
7. Download the draft assets. Run `shasum -a 256 -c SHA256SUMS`. Open the app once on a clean user account.
8. Publish the release.

Repository settings (once): enable private vulnerability reporting, Dependabot alerts and secret scanning with push
protection. Set the Actions workflow permissions to read-only. Protect `main`: require a pull request and the status
checks, block force pushes.
