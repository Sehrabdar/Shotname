"""
tests/test_hardening.py
-----------------------
Focused edge-case and error-handling tests for Step 6 hardening.

Covers gaps not addressed by earlier test files:
  - naming.py: null-byte sanitization, empty-stem resolve_collision.
  - popup.py: TclError-safe destroy, non-string inputs, Tk() init failure.
  - clipboard.py: file-disappears-before-copy.
  - cli._process: outer exception guard keeps loop alive.
  - cli._do_rename: path-escapes-folder guard, unlink failure non-fatal.
  - watcher: renamed file not re-detected (loop prevention).
"""

import os
import tempfile
import types
import sys
import unittest
from unittest.mock import MagicMock, patch, call

from shotname.naming import resolve_collision, sanitize_user_name
from shotname import clipboard
from shotname.popup import PopupResult, ask_filename


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _png(path: str) -> None:
    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)


# ===========================================================================
# naming.py hardening
# ===========================================================================
class TestSanitizeNullByte(unittest.TestCase):

    def test_null_byte_stripped(self):
        result = sanitize_user_name("my\x00file")
        self.assertNotIn("\x00", result)
        self.assertEqual(result, "myfile")

    def test_only_null_byte_returns_empty(self):
        self.assertEqual(sanitize_user_name("\x00"), "")

    def test_null_byte_before_extension(self):
        result = sanitize_user_name("file\x00.png")
        self.assertNotIn("\x00", result)


class TestResolveCollisionEmptyStem(unittest.TestCase):

    def test_empty_stem_produces_safe_name(self):
        """An empty stem must not produce '.png' — uses 'untitled' fallback."""
        result = resolve_collision("", set())
        self.assertNotEqual(result, ".png")
        self.assertTrue(result.endswith(".png"))
        self.assertGreater(len(result), 4)   # more than just ".png"

    def test_empty_stem_collision(self):
        result1 = resolve_collision("", set())
        existing = {result1}
        result2 = resolve_collision("", existing)
        self.assertNotEqual(result1, result2)
        self.assertNotIn(result2, existing)


# ===========================================================================
# popup.py hardening
# ===========================================================================

def _build_tk_mock_safe():
    """Same as test_popup's helper but also captures _safe_destroy usage."""
    tk_mod = MagicMock(name="tkinter")
    font_mod = MagicMock(name="tkinter.font")
    root = MagicMock(name="Tk()")
    root.winfo_reqwidth.return_value = 400
    root.winfo_reqheight.return_value = 150
    root.winfo_screenwidth.return_value = 1920
    root.winfo_screenheight.return_value = 1080

    after_cbs: dict = {}

    def _after(ms, cb=None, *args):
        if cb is not None:
            after_cbs[ms] = cb
        return "id"

    root.after.side_effect = _after

    bindings: dict = {}
    root.bind.side_effect = lambda seq, cb: bindings.__setitem__(seq, cb)

    entry_var = MagicMock()
    entry_var.get.return_value = "suggested"
    tk_mod.Tk.return_value = root
    tk_mod.StringVar.return_value = entry_var
    tk_mod.Label.return_value = MagicMock()
    tk_mod.Entry.return_value = MagicMock()
    tk_mod.Frame.return_value = MagicMock()
    tk_mod.Button.return_value = MagicMock()

    return tk_mod, font_mod, root, entry_var, after_cbs, bindings


class TestPopupTclErrorResilience(unittest.TestCase):

    def test_destroy_raises_tclError_on_double_close(self):
        """
        If root.destroy() raises TclError (window already gone),
        the popup must still return a valid result without propagating.
        """
        tk_mod, font_mod, root, entry_var, after_cbs, bindings = _build_tk_mock_safe()

        # Make destroy() raise on second call (simulates already-destroyed window)
        call_count = {"n": 0}

        def _destroy_raises_second():
            call_count["n"] += 1
            if call_count["n"] > 1:
                from unittest.mock import MagicMock
                raise Exception("TclError: application has been destroyed")

        root.destroy.side_effect = _destroy_raises_second

        def _mainloop():
            # Fire Enter then timeout — second destroy must be safe
            bindings["<Return>"](None)
            timeout_key = max(after_cbs.keys())
            after_cbs[timeout_key]()   # second close – must not raise

        root.mainloop.side_effect = _mainloop

        with patch.dict(sys.modules, {"tkinter": tk_mod, "tkinter.font": font_mod}):
            result = ask_filename("suggested", timeout=5.0)

        # First action (Enter) wins; no exception propagated
        self.assertTrue(result.accepted)

    def test_tk_init_failure_returns_cancel(self):
        """If tk.Tk() raises, ask_filename must return cancel gracefully."""
        tk_mod = MagicMock(name="tkinter")
        font_mod = MagicMock(name="tkinter.font")
        tk_mod.Tk.side_effect = Exception("no display")

        with patch.dict(sys.modules, {"tkinter": tk_mod, "tkinter.font": font_mod}):
            result = ask_filename("x", timeout=8.0)

        self.assertFalse(result.accepted)
        self.assertEqual(result.action, "cancel")

    def test_non_string_suggested_handled(self):
        """ask_filename must not crash when suggested is not a string."""
        tk_mod, font_mod, root, entry_var, after_cbs, bindings = _build_tk_mock_safe()
        root.mainloop.side_effect = lambda: bindings["<Escape>"](None)

        with patch.dict(sys.modules, {"tkinter": tk_mod, "tkinter.font": font_mod}):
            result = ask_filename(None, timeout=8.0)   # type: ignore[arg-type]

        self.assertEqual(result.action, "cancel")

    def test_zero_timeout_uses_minimum(self):
        """A timeout of 0 or negative must not crash (clamped to minimum 1ms)."""
        tk_mod, font_mod, root, entry_var, after_cbs, bindings = _build_tk_mock_safe()
        root.mainloop.side_effect = lambda: bindings["<Escape>"](None)

        with patch.dict(sys.modules, {"tkinter": tk_mod, "tkinter.font": font_mod}):
            result = ask_filename("x", timeout=0)

        # max(1, int(0 * 1000)) == 1  — no crash
        self.assertEqual(result.action, "cancel")


# ===========================================================================
# clipboard.py hardening
# ===========================================================================
class TestClipboardMissingFile(unittest.TestCase):

    def setUp(self):
        clipboard._missing_warned.clear()

    def test_wayland_missing_file_returns_false(self):
        """If the image file disappears before wl-copy reads it, return False."""
        with patch.object(clipboard, "detect_session", return_value="wayland"), \
             patch("shutil.which", return_value="/usr/bin/wl-copy"), \
             patch("builtins.open", side_effect=FileNotFoundError("gone")):
            result = clipboard.copy_image("/tmp/vanished.png")
        self.assertFalse(result)

    def test_x11_popen_oserror_returns_false(self):
        """If Popen raises OSError (e.g. xclip exec fails), return False."""
        with patch.object(clipboard, "detect_session", return_value="x11"), \
             patch("shutil.which", return_value="/usr/bin/xclip"), \
             patch("subprocess.Popen", side_effect=OSError("exec failed")):
            result = clipboard.copy_image("/tmp/shot.png")
        self.assertFalse(result)


# ===========================================================================
# cli._process outer exception guard
# ===========================================================================
class TestProcessOuterGuard(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = self.tmp.name
        clipboard._missing_warned.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def test_unexpected_exception_does_not_propagate(self):
        """
        An unexpected exception inside _process_inner must be caught by the
        outer guard in _process so the watcher loop continues.
        """
        from shotname.cli import _process
        path = os.path.join(self.folder, "Screenshot from 2026-11-21 12-00-00.png")
        _png(path)

        with patch("shotname.cli._process_inner",
                   side_effect=RuntimeError("completely unexpected")), \
             patch("shotname.clipboard.copy_image", return_value=False):
            # Must not raise
            _process(path, self.folder, popup_timeout=8.0)

    def test_outer_guard_attempts_clipboard_on_error(self):
        """When _process_inner crashes, clipboard is still attempted with original."""
        from shotname.cli import _process
        path = os.path.join(self.folder, "Screenshot from 2026-11-21 12-00-00.png")
        _png(path)

        with patch("shotname.cli._process_inner",
                   side_effect=RuntimeError("boom")), \
             patch("shotname.clipboard.copy_image", return_value=False) as mock_clip:
            _process(path, self.folder, popup_timeout=8.0)

        mock_clip.assert_called_once_with(path)


# ===========================================================================
# cli._do_rename hardening
# ===========================================================================
class TestDoRenameHardening(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _make(self, name: str) -> str:
        path = os.path.join(self.folder, name)
        _png(path)
        return path

    def test_path_escapes_folder_blocked(self):
        """A stem containing .. after sanitization must not escape the folder."""
        from shotname.cli import _do_rename
        src = self._make("Screenshot from 2026-11-21 12-00-00.png")
        # Sanitize_user_name strips .. but test _do_rename directly with a weird path
        # that normpath would resolve outside folder.  We simulate this by patching
        # resolve_collision to return "../evil".
        with patch("shotname.cli.resolve_collision", return_value="../evil.png"):
            final, ok = _do_rename(src, "stem", self.folder)
        self.assertFalse(ok)
        self.assertEqual(final, src)
        self.assertTrue(os.path.exists(src))

    def test_unlink_failure_still_returns_success(self):
        """
        If os.unlink fails after a successful os.link, the new file exists and
        rename is treated as succeeded (data is not lost).
        """
        from shotname.cli import _do_rename
        src = self._make("Screenshot from 2026-11-21 12-00-00.png")

        with patch("os.unlink", side_effect=OSError("unlink denied")):
            final, ok = _do_rename(src, "2026-11-21_12-00-00", self.folder)

        # Both src and dst exist (src wasn't removed but dst was created)
        self.assertTrue(ok)
        self.assertTrue(os.path.exists(final))

    def test_file_disappears_before_link(self):
        """
        If the source file disappears before os.link is called,
        _do_rename returns (src, False) without crashing.
        """
        from shotname.cli import _do_rename
        src = os.path.join(self.folder, "Screenshot from 2026-11-21 12-00-00.png")
        # Don't create the file — simulate disappearance
        final, ok = _do_rename(src, "2026-11-21_12-00-00", self.folder)
        self.assertFalse(ok)
        self.assertEqual(final, src)


# ===========================================================================
# Watcher loop prevention – renamed file not re-detected
# ===========================================================================
class TestWatcherLoopPrevention(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_renamed_non_matching_file_not_detected(self):
        """
        After renaming Screenshot*.png → YYYY-MM-DD_*.png, the new name
        does not match the Screenshot*.png pattern and is never queued.
        """
        from shotname.watcher import ScreenshotWatcher
        cb = MagicMock()
        w = ScreenshotWatcher(self.folder, cb, poll_interval=0.05)

        # Create a renamed file (doesn't match Screenshot*.png)
        renamed = os.path.join(self.folder, "2026-11-21_10-32-11.png")
        _png(renamed)

        for _ in range(4):
            w._tick()

        cb.assert_not_called()

    def test_original_path_in_handled_prevents_redetection(self):
        """
        Even if the original path somehow still matches the pattern
        and hasn't been physically removed, it won't be re-emitted.
        """
        from shotname.watcher import ScreenshotWatcher
        cb = MagicMock()
        w = ScreenshotWatcher(self.folder, cb, poll_interval=0.05)

        path = os.path.join(self.folder, "Screenshot test.png")
        _png(path)

        # Stabilise and emit once
        w._tick(); w._tick()
        self.assertEqual(cb.call_count, 1)

        # Tick many more times – callback must not fire again
        for _ in range(6):
            w._tick()
        self.assertEqual(cb.call_count, 1)


if __name__ == "__main__":
    unittest.main()

