from __future__ import annotations

import importlib
import json
import os
import sys
import traceback
from typing import Callable

from diarchy_host import kvm, probe, report
from diarchy_host.common import AgentError, agent_version

SCHEMA = 1
EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 64
USAGE = "usage: diarchy-host {version|probe|report|kvm|stream} ..."
# command -> its known sub-commands (None: any non-option token is reported, the module validates it)
SUBCOMMANDED: dict[str, tuple[str, ...] | None] = {"kvm": kvm.SUBCOMMANDS, "stream": None}


def _version(args: list[str]) -> dict:
    if args:
        raise AgentError("usage", f"version takes no arguments\n{USAGE}")
    return {"agent_version": agent_version()}


def _stream(args: list[str]) -> dict:
    try:
        stream = importlib.import_module("diarchy_host.stream")
    except ModuleNotFoundError as error:
        if error.name != "diarchy_host.stream":
            raise
        raise AgentError("not_installed", "streaming support is not installed") from error
    return stream.handle(args)


DISPATCH: dict[str, Callable[[list[str]], dict]] = {
    "version": _version,
    "probe": probe.handle,
    "report": report.handle,
    "kvm": kvm.handle,
}


def _command_name(argv: list[str]) -> str:
    """R2: first non-option token, plus the sub-command for `kvm`/`stream`."""
    tokens = [token for token in argv if not token.startswith("-")]
    if not tokens:
        return ""
    if tokens[0] in SUBCOMMANDED and len(argv) > 1 and not argv[1].startswith("-"):
        known = SUBCOMMANDED[tokens[0]]
        if known is None or argv[1] in known:
            return f"{tokens[0]} {argv[1]}"
    return tokens[0]


def _failure(command: str, code: str, message: str) -> dict:
    return {"schema": SCHEMA, "ok": False, "command": command, "error": {"code": code, "message": message}}


def _dispatch(argv: list[str]) -> dict:
    if not argv:
        raise AgentError("usage", USAGE)
    name, args = argv[0], argv[1:]
    if name.startswith("-"):
        raise AgentError("usage", USAGE)
    if name == "stream":
        return _stream(args)
    handler = DISPATCH.get(name)
    if handler is None:
        raise AgentError("usage", f"unknown command: {name}\n{USAGE}")
    return handler(args)


def _success(command: str, payload: dict) -> dict:
    envelope = {"schema": SCHEMA, "ok": True, "command": command}
    reserved = envelope.keys() & payload.keys()
    if reserved:
        raise AgentError("internal", f"handler payload overrides envelope keys: {', '.join(sorted(reserved))}")
    envelope.update(payload)
    return envelope


def _serialize(envelope: dict) -> str:
    return json.dumps(envelope, separators=(",", ":")) + "\n"


def _execute(argv: list[str]) -> tuple[str, int]:
    """Return the single stdout line and the exit code; never raises."""
    command = _command_name(argv)
    real_stdout = sys.stdout
    sys.stdout = sys.stderr  # R6: stray prints can never corrupt the single JSON line
    try:
        try:
            return _serialize(_success(command, _dispatch(argv))), EXIT_OK
        except AgentError as error:
            code = EXIT_USAGE if error.code == "usage" else EXIT_FAILURE
            return _serialize(_failure(command, error.code, error.message)), code
        except Exception as error:  # noqa: BLE001 - the envelope contract must survive any bug
            traceback.print_exc()
            failure = _failure(command, "internal", f"{type(error).__name__}: {error}")
            return _serialize(failure), EXIT_FAILURE
    finally:
        sys.stdout = real_stdout


def main(argv: list[str]) -> int:
    os.environ.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")  # R3
    line, code = _execute(argv)
    sys.stdout.write(line)
    sys.stdout.flush()
    return code
