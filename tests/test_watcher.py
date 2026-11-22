"""
tests/test_watcher.py
---------------------
Unit tests for shotname/watcher.py.

Strategy
--------
All tests run against the ScreenshotWatcher internals (``_tick``,
``_advance_stability``, ``_try_emit``) directly – we never call ``run()``
so we avoid real time.sleep().  A temporary directory (tempfile.TemporaryDirectory)
acts as the fake screenshots folder.

Standard-library unittest only; no pytest, no mocks library beyond
unittest.mock which ships with Python 3.3+.
"""

import os
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from shotname.watcher import (
    OPEN_RETRIES,
    OPEN_RETRY_DELAY,
    POLL_INTERVAL,
    STABILITY_CHECKS,
    ScreenshotWatcher,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_watcher(folder, callback=None, stability_checks=2, open_retries=5,
                  open_retry_delay=0.0):
    """Return a ScreenshotWatcher with sane test defaults (no sleep delays)."""
    if callback is None:
        callback = MagicMock()
    return ScreenshotWatcher(
        folder=folder,
        callback=callback,
        poll_interval=0.05,            # fast but not relevant – we call _tick
        stability_checks=stability_checks,
        open_retries=open_retries,
        open_retry_delay=open_retry_delay,
    )


def _write(path: str, content: bytes = b"PNG\x00data") -> None:
    with open(path, "wb") as fh:
        fh.write(content)


# ===========================================================================
# Constants
# ===========================================================================
class TestConstants(unittest.TestCase):

    def test_poll_interval(self):
        self.assertEqual(POLL_INTERVAL, 0.5)

    def test_stability_checks(self):
        self.assertEqual(STABILITY_CHECKS, 2)

    def test_open_retries(self):
        self.assertEqual(OPEN_RETRIES, 5)

    def test_open_retry_delay(self):
        self.assertEqual(OPEN_RETRY_DELAY, 0.3)


# ===========================================================================
# Basic detection
# ===========================================================================
class TestBasicDetection(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _stable_path(self, watcher, path):
        """Drive the watcher through two equal-size polls so the file stabilises."""
        watcher._tick()   # first sighting – equal_count becomes 1
        watcher._tick()   # second sighting – equal_count becomes 2 → emit

    def test_screenshot_detected(self):
        """A Screenshot*.png file is detected and the callback is called."""
        cb = MagicMock()
        w = _make_watcher(self.folder, cb)
        path = os.path.join(self.folder, "Screenshot test.png")
        _write(path)
        self._stable_path(w, path)
        cb.assert_called_once_with(path)

    def test_emitted_only_once(self):
        """A file already in handled is never emitted again."""
        cb = MagicMock()
        w = _make_watcher(self.folder, cb)
        path = os.path.join(self.folder, "Screenshot test.png")
        _write(path)
        # First stabilisation cycle
        self._stable_path(w, path)
        # Tick many more times – callback must not be called again
        for _ in range(5):
            w._tick()
        cb.assert_called_once_with(path)

    def test_dotfile_ignored(self):
        """A dotfile that matches the pattern is ignored."""
        cb = MagicMock()
        w = _make_watcher(self.folder, cb)
        path = os.path.join(self.folder, ".Screenshot hidden.png")
        _write(path)
        for _ in range(4):
            w._tick()
        cb.assert_not_called()

    def test_non_png_ignored(self):
        """A file that doesn't match Screenshot*.png is ignored."""
        cb = MagicMock()
        w = _make_watcher(self.folder, cb)
        for name in ["Screenshot test.jpg", "Screenshot test.bmp",
                     "document.png", "notes.txt"]:
            _write(os.path.join(self.folder, name))
        for _ in range(4):
            w._tick()
        cb.assert_not_called()

    def test_multiple_screenshots_all_detected(self):
        """Every new screenshot in the folder is detected independently."""
        cb = MagicMock()
        w = _make_watcher(self.folder, cb)
        paths = [
            os.path.join(self.folder, f"Screenshot {i}.png") for i in range(3)
        ]
        for p in paths:
            _write(p)
        for _ in range(3):
            w._tick()
        self.assertEqual(cb.call_count, 3)
        called_with = {args[0] for args, _ in cb.call_args_list}
        self.assertEqual(called_with, set(paths))


# ===========================================================================
# Stability detection
# ===========================================================================
class TestFileStability(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_growing_file_not_emitted_until_stable(self):
        """
        A file whose size changes between polls must NOT be emitted until
        two consecutive polls show the same size.

        Tick-by-tick state (stability_checks=2):
          tick1: size=10, last=-1  → size≠last → equal_count=1, pending=(10,1)
          tick2: size=20, last=10  → size≠last → equal_count=1, pending=(20,1)  [not called]
          tick3: size=20, last=20  → size==last → equal_count=2 ≥ 2 → emit
        """
        cb = MagicMock()
        w = _make_watcher(self.folder, cb, stability_checks=2)
        path = os.path.join(self.folder, "Screenshot growing.png")

        # tick1: file first seen at 10 bytes
        _write(path, b"A" * 10)
        w._tick()
        cb.assert_not_called()          # equal_count=1, not yet stable

        # tick2: file has grown to 20 bytes → counter resets
        _write(path, b"A" * 20)
        w._tick()
        cb.assert_not_called()          # size changed → equal_count reset to 1

        # tick3: same 20-byte size again → equal_count=2 → stable → emit
        w._tick()
        cb.assert_called_once_with(path)

    def test_first_sight_does_not_emit(self):
        """A file seen for the first time must not be emitted immediately."""
        cb = MagicMock()
        w = _make_watcher(self.folder, cb, stability_checks=2)
        path = os.path.join(self.folder, "Screenshot first.png")
        _write(path)
        w._tick()
        cb.assert_not_called()

    def test_stability_requires_configured_checks(self):
        """With stability_checks=3, a file needs three equal-size polls."""
        cb = MagicMock()
        w = _make_watcher(self.folder, cb, stability_checks=3)
        path = os.path.join(self.folder, "Screenshot triple.png")
        _write(path)

        w._tick();  cb.assert_not_called()   # equal_count=1
        w._tick();  cb.assert_not_called()   # equal_count=2
        w._tick();  cb.assert_called_once()  # equal_count=3 → emit


# ===========================================================================
# Handled-paths / loop prevention
# ===========================================================================
class TestHandledPaths(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_path_added_to_handled_after_emit(self):
        cb = MagicMock()
        w = _make_watcher(self.folder, cb)
        path = os.path.join(self.folder, "Screenshot h.png")
        _write(path)
        for _ in range(3):
            w._tick()
        self.assertIn(path, w.handled)

    def test_pre_populated_handled_not_re_emitted(self):
        """Files already in handled before the watcher starts are skipped."""
        cb = MagicMock()
        w = _make_watcher(self.folder, cb)
        path = os.path.join(self.folder, "Screenshot old.png")
        _write(path)
        w.handled.add(path)             # pre-populate
        for _ in range(4):
            w._tick()
        cb.assert_not_called()

    def test_handled_even_when_callback_raises(self):
        """A crashing callback must not remove the path from handled."""
        def bad_cb(p):
            raise RuntimeError("Boom!")

        w = _make_watcher(self.folder, bad_cb)
        path = os.path.join(self.folder, "Screenshot crash.png")
        _write(path)
        for _ in range(3):
            w._tick()
        self.assertIn(path, w.handled)

    def test_preexisting_files_not_emitted_at_startup(self):
        """Files already in the folder when the watcher is created must NOT be processed."""
        cb = MagicMock()
        # Create the file BEFORE creating the watcher
        path = os.path.join(self.folder, "Screenshot existing.png")
        _write(path)
        # Now create the watcher – the pre-existing file should be pre-seeded
        w = _make_watcher(self.folder, cb)
        # Tick many times – the pre-existing file must never trigger the callback
        for _ in range(5):
            w._tick()
        cb.assert_not_called()

    def test_new_file_after_startup_is_emitted(self):
        """A file created AFTER the watcher starts must still be detected normally."""
        cb = MagicMock()
        # Create an old file before the watcher
        old = os.path.join(self.folder, "Screenshot old.png")
        _write(old)
        w = _make_watcher(self.folder, cb)
        # Now create a NEW file after watcher init
        new = os.path.join(self.folder, "Screenshot new.png")
        _write(new)
        # Drive two ticks to stabilise the new file
        w._tick()
        w._tick()
        cb.assert_called_once_with(new)

    def test_own_renamed_file_not_re_processed(self):
        """
        Simulate the app renaming a file: the new name should not be
        detected as a new screenshot if it doesn't match Screenshot*.png,
        and even if it does, pre-populating handled prevents it.
        """
        cb = MagicMock()
        w = _make_watcher(self.folder, cb)
        # Renamed file doesn't start with 'Screenshot' → never matched
        renamed = os.path.join(self.folder, "2026-11-21_invoice.png")
        _write(renamed)
        for _ in range(4):
            w._tick()
        cb.assert_not_called()


# ===========================================================================
# Resilience – vanishing / inaccessible files
# ===========================================================================
class TestResilience(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_vanishing_file_does_not_crash_watcher(self):
        """
        A file that disappears mid-check must not raise an unhandled exception.
        """
        cb = MagicMock()
        w = _make_watcher(self.folder, cb)
        path = os.path.join(self.folder, "Screenshot vanish.png")
        _write(path)
        w._tick()           # first sighting
        os.remove(path)     # simulate disappearance
        # Must not raise
        w._tick()
        cb.assert_not_called()   # never got stable

    def test_missing_folder_does_not_crash_watcher(self):
        """
        If the watched folder is gone, _tick() should log an error but not
        raise.
        """
        cb = MagicMock()
        w = _make_watcher("/nonexistent_folder_99999", cb)
        # Must not raise
        w._tick()
        cb.assert_not_called()

    def test_permission_error_on_open_retries_then_gives_up(self):
        """
        When open() raises PermissionError every time, the watcher retries
        up to open_retries times, then marks the file as handled and gives up.
        """
        cb = MagicMock()
        w = _make_watcher(self.folder, cb, open_retries=3, open_retry_delay=0.0)
        path = os.path.join(self.folder, "Screenshot perm.png")
        _write(path)

        with patch("builtins.open", side_effect=PermissionError("denied")):
            w._try_emit(path)

        cb.assert_not_called()
        self.assertIn(path, w.handled)   # still marked so not retried forever

    def test_file_not_found_on_open_retries_then_gives_up(self):
        """
        When open() raises FileNotFoundError every time, same give-up logic
        applies.
        """
        cb = MagicMock()
        w = _make_watcher(self.folder, cb, open_retries=2, open_retry_delay=0.0)
        path = os.path.join(self.folder, "Screenshot gone.png")

        with patch("builtins.open", side_effect=FileNotFoundError("gone")):
            w._try_emit(path)

        cb.assert_not_called()
        self.assertIn(path, w.handled)

    def test_open_succeeds_after_one_retry(self):
        """
        If the first open() raises PermissionError but the second succeeds,
        the callback is still invoked.
        """
        cb = MagicMock()
        w = _make_watcher(self.folder, cb, open_retries=3, open_retry_delay=0.0)
        path = os.path.join(self.folder, "Screenshot retry.png")
        _write(path)

        call_count = {"n": 0}
        real_open = open

        def flaky_open(p, *args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise PermissionError("first attempt fails")
            return real_open(p, *args, **kwargs)

        with patch("builtins.open", side_effect=flaky_open):
            w._try_emit(path)

        cb.assert_called_once_with(path)
        self.assertIn(path, w.handled)


# ===========================================================================
# Stop / run-loop control
# ===========================================================================
class TestRunLoop(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_stop_exits_run(self):
        """
        Calling stop() from another thread causes run() to exit within a
        reasonable time.
        """
        cb = MagicMock()
        w = ScreenshotWatcher(self.folder, cb, poll_interval=0.05)

        t = threading.Thread(target=w.run, daemon=True)
        t.start()
        time.sleep(0.15)     # let it run a few ticks
        w.stop()
        t.join(timeout=2.0)
        self.assertFalse(t.is_alive(), "run() did not exit after stop()")


if __name__ == "__main__":
    unittest.main()
