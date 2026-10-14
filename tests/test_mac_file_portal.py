from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "client" / "Diarchy File Portal.swift"
STAGING_SOURCE = ROOT / "client" / "File Portal Staging.swift"


class MacFilePortalTests(unittest.TestCase):
    def test_source_uses_native_argument_safe_file_drop_contract(self) -> None:
        source = SOURCE.read_text()
        self.assertIn("registerForDraggedTypes([.fileURL])", source)
        self.assertIn("readObjects(forClasses:", source)
        self.assertIn(".urlReadingFileURLsOnly", source)
        self.assertIn("process.executableURL", source)
        self.assertIn('process.arguments = ["--"]', source)
        self.assertIn("DIARCHY_FILE_PORTAL_SENDER_BIN", source)
        self.assertIn("portal-send name=", source)
        self.assertNotIn('executableURL = URL(fileURLWithPath: "/bin/sh")', source)
        self.assertNotIn("shell", source.lower())

    def test_source_uses_transparent_edge_trigger_and_defers_safe_copy_to_sender(self) -> None:
        source = SOURCE.read_text()
        self.assertIn("screenFrame.minX", source)
        self.assertNotIn("visibleFrame.minX", source)
        self.assertIn("triggerSize = NSSize(width: 3, height: 180)", source)
        self.assertIn("revealedSize = NSSize(width: 52, height: 180)", source)
        self.assertIn("expandedSize = NSSize(width: 72, height: 200)", source)
        self.assertIn(".mouseEnteredAndExited", source)
        self.assertIn("deadline: .now() + 0.5", source)
        self.assertIn("layer?.opacity = 0", source)
        self.assertNotIn("FileManager.default.enumerator", source)
        self.assertIn(".accessory", source)
        self.assertIn(".nonactivatingPanel", source)
        self.assertIn("override func constrainFrameRect", source)
        self.assertIn("return frameRect", source)
        self.assertIn("label.autoresizingMask = [.width, .height]", source)
        self.assertNotIn("NSLayoutConstraint.activate", source)

    def test_external_file_drag_monitor_reveals_the_collapsed_edge(self) -> None:
        source = SOURCE.read_text()
        self.assertIn("private var edgeDragMonitor: Any?", source)
        self.assertIn("NSEvent.addGlobalMonitorForEvents", source)
        self.assertIn("edgeDragActivationWidth: CGFloat = 120", source)
        self.assertIn(".leftMouseDragged", source)
        self.assertIn(".leftMouseUp", source)
        self.assertIn("NSEvent.removeMonitor", source)
        self.assertIn("NSEvent.mouseLocation", source)
        self.assertIn("NSPasteboard(name: .drag)", source)
        self.assertIn("canReadObject(", source)
        self.assertIn("forClasses: [NSURL.self]", source)
        self.assertIn("reveal(expanded: true)", source)

    def test_sender_is_owned_and_cancelled_with_portal_lifecycle(self) -> None:
        source = SOURCE.read_text()
        self.assertIn("DIARCHY_FILE_PORTAL_PARENT_PID", source)
        self.assertIn("track(process)", source)
        self.assertIn("untrack(process)", source)
        self.assertIn("cancelActiveTransfers()", source)
        self.assertIn("applicationWillTerminate", source)
        self.assertIn("process.waitUntilExit()", source)
        self.assertIn("SIGKILL", source)

    def test_drop_is_copied_to_isolated_staging_before_sender_access(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmpdir:
            tmpdir = Path(raw_tmpdir)
            source_root = tmpdir / "source"
            source_root.mkdir()
            source_file = source_root / "protected.txt"
            source_file.write_text("protected payload")
            source_directory = source_root / "folder"
            source_directory.mkdir()
            (source_directory / "nested.txt").write_text("nested payload")
            (source_directory / "link.txt").symlink_to("../protected.txt")
            source_link = source_root / "protected-link.txt"
            source_link.symlink_to("protected.txt")
            harness = tmpdir / "main.swift"
            harness.write_text(
                """
                import Foundation

                let sources = CommandLine.arguments.dropFirst().map {
                    URL(fileURLWithPath: $0)
                }
                let access = DroppedItemAccess(urls: sources)
                let staging = try access.stage()
                let originalParent = sources[0].deletingLastPathComponent().standardizedFileURL
                let stagedParent = staging.urls[0].deletingLastPathComponent().standardizedFileURL
                print(stagedParent == originalParent ? "original-parent" : "isolated-parent")
                print(try String(contentsOf: staging.urls[0], encoding: .utf8))
                let nested = staging.urls[1].appendingPathComponent("nested.txt")
                print(try String(contentsOf: nested, encoding: .utf8))
                let nestedLink = staging.urls[1].appendingPathComponent("link.txt")
                print(try FileManager.default.destinationOfSymbolicLink(atPath: nestedLink.path))
                print(try FileManager.default.destinationOfSymbolicLink(atPath: staging.urls[2].path))
                let stagingPath = staging.rootURL.path
                staging.remove()
                print(FileManager.default.fileExists(atPath: stagingPath) ? "retained" : "removed")
                """
            )
            executable = tmpdir / "staging-contract"
            compiled = subprocess.run(
                [
                    "xcrun",
                    "swiftc",
                    str(STAGING_SOURCE),
                    str(harness),
                    "-o",
                    str(executable),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)

            result = subprocess.run(
                [str(executable), str(source_file), str(source_directory), str(source_link)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.splitlines(),
                [
                    "isolated-parent",
                    "protected payload",
                    "nested payload",
                    "../protected.txt",
                    "protected.txt",
                    "removed",
                ],
            )

    def test_source_typechecks(self) -> None:
        result = subprocess.run(
            [
                "xcrun",
                "swiftc",
                "-typecheck",
                "-framework",
                "AppKit",
                str(STAGING_SOURCE),
                str(SOURCE),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
