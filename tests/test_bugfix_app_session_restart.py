from __future__ import annotations

import runpy
import threading
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECEIVER = ROOT / "client" / "diarchy-file-portal-receiver"
BRIDGE = ROOT / "client" / "diarchy-clipboard-bridge"


def load_bridge() -> types.ModuleType:
    source = BRIDGE.read_text().split("# PYTHON_MODULE_START\n", 1)[1]
    module = types.ModuleType("diarchy_clipboard_bridge")
    exec(compile(source, str(BRIDGE), "exec"), module.__dict__)
    return module


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


class SuperviseContract:
    """Both long ssh sessions restart on exit with a capped exponential backoff."""

    def supervise(self):
        raise NotImplementedError

    def drive(self, outcomes: list[tuple[int, float]], stop_after: int):
        supervise = self.supervise()
        stop = threading.Event()
        clock = Clock()
        sleeps: list[float] = []
        calls: list[int] = []

        def run_once() -> int:
            index = len(calls)
            calls.append(index)
            code, lifetime = outcomes[min(index, len(outcomes) - 1)]
            clock.value += lifetime
            if len(calls) >= stop_after:
                stop.set()
            return code

        def sleep(seconds: float) -> None:
            sleeps.append(seconds)
            return None

        result = supervise(run_once, stop, sleep=sleep, now=clock)
        return result, calls, sleeps

    def test_restarts_after_each_exit_with_doubling_capped_backoff(self) -> None:
        _, calls, sleeps = self.drive([(1, 0.1)], stop_after=8)
        self.assertEqual(len(calls), 8)
        self.assertEqual(sleeps, [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0])

    def test_a_clean_exit_also_restarts(self) -> None:
        _, calls, _ = self.drive([(0, 0.1)], stop_after=3)
        self.assertEqual(len(calls), 3)

    def test_a_long_healthy_session_resets_the_backoff(self) -> None:
        _, _, sleeps = self.drive([(1, 0.1), (1, 0.1), (1, 120.0), (1, 0.1)], stop_after=4)
        self.assertEqual(sleeps, [1.0, 2.0, 1.0])

    def test_stop_ends_the_loop_without_another_start(self) -> None:
        result, calls, sleeps = self.drive([(7, 0.1)], stop_after=1)
        self.assertEqual((len(calls), sleeps), (1, []))
        self.assertEqual(result, 7)


class ReceiverSuperviseTests(SuperviseContract, unittest.TestCase):
    def supervise(self):
        return runpy.run_path(str(RECEIVER))["supervise"]


class BridgeSuperviseTests(SuperviseContract, unittest.TestCase):
    def supervise(self):
        return load_bridge().supervise


if __name__ == "__main__":
    unittest.main()
