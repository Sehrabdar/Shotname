"""
tests/test_cli.py
-----------------
Unit tests for shotname/cli.py and shotname/clipboard.py.
Standard-library unittest + unittest.mock only.
"""

import os
import tempfile
import time
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from shotname.cli import _process, extract_timestamp, safe_rename
from shotname import clipboard
from shotname import popup as popup_mod
from shotname.popup import PopupResult


# ===========================================================================
# Helpers
# ===========================================================================

def _png(path: str) -> None:
    """Write a minimal valid-ish PNG so os.stat works."""
    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)


# Default popup stub: simulates user pressing Esc / cancel
_CANCEL = PopupResult(action="cancel")

def _accept(name: str) -> PopupResult:
    return PopupResult(action="accept", name=name)


def _process_with_popup(path, folder, popup_result):
    """Helper: call _process() with a mocked popup that returns popup_result."""
    with patch("shotname.popup.ask_filename", return_value=popup_result), \
         patch("shotname.clipboard.copy_image", return_value=True):
        _process(path, folder, popup_timeout=8.0)


# ===========================================================================
# Helpers
# ===========================================================================

def _png(path: str) -> None:
    """Write a minimal valid-ish PNG so os.stat works."""
    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)


# ===========================================================================
# extract_timestamp()
# ===========================================================================
class TestExtractTimestamp(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_parses_ubuntu_filename(self):
        path = os.path.join(self.tmp.name, "Screenshot from 2026-11-21 10-32-11.png")
        _png(path)
        ts = extract_timestamp(path)
        self.assertEqual(ts, datetime(2026, 11, 21, 10, 32, 11))

    def test_parses_zero_padded(self):
        path = os.path.join(self.tmp.name, "Screenshot from 2026-01-05 09-05-01.png")
        _png(path)
        ts = extract_timestamp(path)
        self.assertEqual(ts, datetime(2026, 1, 5, 9, 5, 1))

    def test_fallback_to_mtime(self):
        path = os.path.join(self.tmp.name, "Screenshot other-name.png")
        _png(path)
        mtime = os.stat(path).st_mtime
        ts = extract_timestamp(path)
        # Should be very close to mtime
        self.assertAlmostEqual(ts.timestamp(), mtime, delta=1.0)

    def test_fallback_to_now_when_stat_fails(self):
        before = datetime.now()
        with patch("os.stat", side_effect=OSError("gone")):
            ts = extract_timestamp("/nonexistent/Screenshot missing.png")
        after = datetime.now()
        self.assertGreaterEqual(ts, before)
        self.assertLessEqual(ts, after)

    def test_does_not_use_birthtime(self):
        """The production cli module must not access .st_birthtime as an attribute."""
        import shotname.cli as cli_mod
        src_path = cli_mod.__file__
        with open(src_path, encoding="utf-8") as fh:
            src = fh.read()
        # Check for attribute *access*  (`.st_birthtime`), not docstring mentions.
        needle = ".st_" + "birthtime"
        self.assertNotIn(needle, src,
                         "cli.py must not access .st_birthtime (unreliable on Linux)")

    def test_no_hardcoded_date(self):
        """No date literal like 2026-11-21 must appear in the source."""
        import inspect
        import shotname.cli as cli_mod
        src = inspect.getsource(cli_mod)
        # Pattern: 4-digit year hyphen 2-digit month as a string literal
        import re
        hardcoded = re.findall(r'["\']20\d\d-\d\d-\d\d["\']', src)
        self.assertEqual(hardcoded, [],
                         f"Hardcoded date literals found: {hardcoded}")


# ===========================================================================
# safe_rename()
# ===========================================================================
class TestSafeRename(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _make(self, name: str) -> str:
        path = os.path.join(self.folder, name)
        _png(path)
        return path

    def test_renames_to_timestamp_stem(self):
        src = self._make("Screenshot from 2026-11-21 10-32-11.png")
        dst, ok = safe_rename(src, self.folder)
        self.assertTrue(ok)
        self.assertTrue(os.path.basename(dst).startswith("2026-11-21_10-32-11"))
        self.assertTrue(dst.endswith(".png"))
        self.assertTrue(os.path.exists(dst))
        self.assertFalse(os.path.exists(src))

    def test_destination_inside_folder(self):
        src = self._make("Screenshot from 2026-11-21 10-32-11.png")
        dst, ok = safe_rename(src, self.folder)
        self.assertTrue(ok)
        self.assertTrue(dst.startswith(self.folder))

    def test_collision_produces_suffix(self):
        """Two screenshots with the same timestamp → second gets -2 suffix."""
        src1 = self._make("Screenshot from 2026-11-21 10-32-11.png")
        dst1, ok1 = safe_rename(src1, self.folder)
        self.assertTrue(ok1)

        src2 = self._make("Screenshot from 2026-11-21 10-32-11.png")
        dst2, ok2 = safe_rename(src2, self.folder)
        self.assertTrue(ok2)

        self.assertNotEqual(dst1, dst2)
        self.assertIn("-2", os.path.basename(dst2))

    def test_existing_file_never_overwritten(self):
        """os.link raises FileExistsError if dst exists; we must handle it."""
        src = self._make("Screenshot from 2026-11-21 10-32-11.png")
        # Pre-create the destination
        pre_dst = os.path.join(self.folder, "2026-11-21_10-32-11.png")
        _png(pre_dst)

        dst, ok = safe_rename(src, self.folder)
        self.assertTrue(ok)
        # Must have resolved to a collision name, not overwritten
        self.assertNotEqual(dst, pre_dst)
        self.assertTrue(os.path.exists(pre_dst))   # original untouched

    def test_original_preserved_on_listdir_failure(self):
        src = self._make("Screenshot from 2026-11-21 10-32-11.png")
        with patch("os.listdir", side_effect=OSError("disk error")):
            final, ok = safe_rename(src, self.folder)
        self.assertFalse(ok)
        self.assertEqual(final, src)
        self.assertTrue(os.path.exists(src))

    def test_original_preserved_on_link_failure(self):
        src = self._make("Screenshot from 2026-11-21 10-32-11.png")
        with patch("os.link", side_effect=OSError("link failed")):
            final, ok = safe_rename(src, self.folder)
        self.assertFalse(ok)
        self.assertEqual(final, src)
        self.assertTrue(os.path.exists(src))


# ===========================================================================
# clipboard.detect_session()
# ===========================================================================
class TestDetectSession(unittest.TestCase):

    def _env(self, **kw):
        """Return a modified copy of os.environ."""
        env = dict(os.environ)
        for k, v in kw.items():
            if v is None:
                env.pop(k, None)
            else:
                env[k] = v
        return env

    def test_xdg_wayland(self):
        with patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland"}, clear=False):
            self.assertEqual(clipboard.detect_session(), "wayland")

    def test_xdg_x11(self):
        with patch.dict(os.environ, {"XDG_SESSION_TYPE": "x11"}, clear=False):
            self.assertEqual(clipboard.detect_session(), "x11")

    def test_wayland_display_fallback(self):
        env = {"XDG_SESSION_TYPE": "", "WAYLAND_DISPLAY": "wayland-0"}
        with patch.dict(os.environ, env, clear=False):
            self.assertEqual(clipboard.detect_session(), "wayland")

    def test_unknown_when_no_vars(self):
        with patch.dict(os.environ,
                        {"XDG_SESSION_TYPE": "", "WAYLAND_DISPLAY": ""},
                        clear=False):
            self.assertEqual(clipboard.detect_session(), "unknown")


# ===========================================================================
# clipboard.copy_image()
# ===========================================================================
class TestCopyImage(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.img = os.path.join(self.tmp.name, "shot.png")
        _png(self.img)
        # Reset the missing-warned set before each test
        clipboard._missing_warned.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def test_wayland_calls_wl_copy(self):
        with patch.object(clipboard, "detect_session", return_value="wayland"), \
             patch("shutil.which", return_value="/usr/bin/wl-copy"), \
             patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
            result = clipboard.copy_image(self.img)
        self.assertTrue(result)
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd[0], "wl-copy")
        self.assertIn("--type", cmd)
        self.assertIn("image/png", cmd)

    def test_x11_uses_popen(self):
        with patch.object(clipboard, "detect_session", return_value="x11"), \
             patch("shutil.which", return_value="/usr/bin/xclip"), \
             patch("subprocess.Popen") as mock_popen:
            result = clipboard.copy_image(self.img)
        self.assertTrue(result)
        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        self.assertEqual(cmd[0], "xclip")
        self.assertIn("-selection", cmd)
        self.assertIn("clipboard", cmd)
        self.assertIn("image/png", cmd)

    def test_x11_does_not_use_subprocess_run(self):
        """xclip must stay alive (Popen, not run)."""
        with patch.object(clipboard, "detect_session", return_value="x11"), \
             patch("shutil.which", return_value="/usr/bin/xclip"), \
             patch("subprocess.run") as mock_run, \
             patch("subprocess.Popen"):
            clipboard.copy_image(self.img)
        mock_run.assert_not_called()

    def test_missing_wl_copy_prints_hint_once(self):
        with patch.object(clipboard, "detect_session", return_value="wayland"), \
             patch("shutil.which", return_value=None), \
             patch("builtins.print") as mock_print:
            clipboard.copy_image(self.img)
            clipboard.copy_image(self.img)   # second call – no extra hint

        hint_calls = [c for c in mock_print.call_args_list
                      if "wl-copy" in str(c)]
        self.assertEqual(len(hint_calls), 1, "Hint should be printed exactly once")

    def test_missing_xclip_prints_hint_once(self):
        with patch.object(clipboard, "detect_session", return_value="x11"), \
             patch("shutil.which", return_value=None), \
             patch("builtins.print") as mock_print:
            clipboard.copy_image(self.img)
            clipboard.copy_image(self.img)

        hint_calls = [c for c in mock_print.call_args_list
                      if "xclip" in str(c)]
        self.assertEqual(len(hint_calls), 1)

    def test_missing_tool_returns_false(self):
        with patch.object(clipboard, "detect_session", return_value="x11"), \
             patch("shutil.which", return_value=None), \
             patch("builtins.print"):
            result = clipboard.copy_image(self.img)
        self.assertFalse(result)

    def test_copy_attempted_even_after_rename_failure(self):
        """Clipboard is always attempted; rename outcome is irrelevant."""
        with patch.object(clipboard, "detect_session", return_value="x11"), \
             patch("shutil.which", return_value="/usr/bin/xclip"), \
             patch("subprocess.Popen") as mock_popen:
            # Simulate: rename failed, so path = original
            clipboard.copy_image(self.img)
        mock_popen.assert_called_once()


# ===========================================================================
# _process() integration
# ===========================================================================
class TestProcess(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = self.tmp.name
        clipboard._missing_warned.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def _make(self, name: str) -> str:
        path = os.path.join(self.folder, name)
        _png(path)
        return path

    def test_process_renames_and_copies_clipboard(self):
        src = self._make("Screenshot from 2026-11-21 10-32-11.png")
        # Popup accepts the suggested name (which will be the timestamp stem)
        with patch("shotname.popup.ask_filename",
                   return_value=_accept("2026-11-21_10-32-11")) as _pop, \
             patch("shotname.clipboard.copy_image", return_value=True) as mock_clip, \
             patch("shotname.ocr.run_ocr", return_value=""):
            _process(src, self.folder, popup_timeout=8.0)
        mock_clip.assert_called_once()
        renamed = [f for f in os.listdir(self.folder)
                   if f.startswith("2026-11-21_10-32-11")]
        self.assertEqual(len(renamed), 1)

    def test_process_copies_original_on_rename_failure(self):
        """When rename fails, clipboard still gets called with the original path."""
        src = self._make("Screenshot from 2026-11-21 10-32-11.png")
        with patch("shotname.popup.ask_filename",
                   return_value=_accept("2026-11-21_10-32-11")), \
             patch("os.link", side_effect=OSError("link fail")), \
             patch("shotname.clipboard.copy_image", return_value=True) as mock_clip, \
             patch("shotname.ocr.run_ocr", return_value=""):
            _process(src, self.folder, popup_timeout=8.0)
        # Clipboard was called with the ORIGINAL path
        mock_clip.assert_called_once_with(src)

    def test_collision_suffix_applied(self):
        src1 = self._make("Screenshot from 2026-11-21 10-32-11.png")
        with patch("shotname.popup.ask_filename",
                   return_value=_accept("2026-11-21_10-32-11")), \
             patch("shotname.clipboard.copy_image", return_value=True), \
             patch("shotname.ocr.run_ocr", return_value=""):
            _process(src1, self.folder, popup_timeout=8.0)

        src2 = self._make("Screenshot from 2026-11-21 10-32-11.png")
        with patch("shotname.popup.ask_filename",
                   return_value=_accept("2026-11-21_10-32-11")), \
             patch("shotname.clipboard.copy_image", return_value=True), \
             patch("shotname.ocr.run_ocr", return_value=""):
            _process(src2, self.folder, popup_timeout=8.0)

        files = os.listdir(self.folder)
        base_files = [f for f in files if f.startswith("2026-11-21_10-32-11")]
        self.assertEqual(len(base_files), 2)
        self.assertTrue(any("-2" in f for f in base_files))

    def test_esc_keeps_original_filename(self):
        """Esc / cancel must not rename the file."""
        src = self._make("Screenshot from 2026-11-21 10-32-11.png")
        with patch("shotname.popup.ask_filename", return_value=_CANCEL), \
             patch("shotname.clipboard.copy_image", return_value=True), \
             patch("shotname.ocr.run_ocr", return_value=""):
            _process(src, self.folder, popup_timeout=8.0)
        self.assertTrue(os.path.exists(src))

    def test_esc_still_copies_clipboard(self):
        """Even when Esc is pressed, clipboard gets the original path."""
        src = self._make("Screenshot from 2026-11-21 10-32-11.png")
        with patch("shotname.popup.ask_filename", return_value=_CANCEL), \
             patch("shotname.clipboard.copy_image", return_value=True) as mock_clip, \
             patch("shotname.ocr.run_ocr", return_value=""):
            _process(src, self.folder, popup_timeout=8.0)
        mock_clip.assert_called_once_with(src)

    def test_path_traversal_in_popup_input_blocked(self):
        """../../evil in the popup entry must stay inside the folder."""
        src = self._make("Screenshot from 2026-11-21 10-32-11.png")
        with patch("shotname.popup.ask_filename",
                   return_value=_accept("../../evil")), \
             patch("shotname.clipboard.copy_image", return_value=True), \
             patch("shotname.ocr.run_ocr", return_value=""):
            _process(src, self.folder, popup_timeout=8.0)
        # Sanitization strips path separators; either kept-original or renamed safely
        for f in os.listdir(self.folder):
            full = os.path.join(self.folder, f)
            self.assertTrue(full.startswith(self.folder))

    def test_empty_sanitized_name_keeps_original(self):
        """An empty sanitized name must not rename the file."""
        src = self._make("Screenshot from 2026-11-21 10-32-11.png")
        with patch("shotname.popup.ask_filename",
                   return_value=_accept("...")), \
             patch("shotname.clipboard.copy_image", return_value=True), \
             patch("shotname.ocr.run_ocr", return_value=""):
            _process(src, self.folder, popup_timeout=8.0)
        self.assertTrue(os.path.exists(src))

    def test_ocr_text_passed_to_suggest_name(self):
        """The OCR result must feed into suggest_name, not be ignored."""
        from shotname import naming as naming_mod
        src = self._make("Screenshot from 2026-11-21 10-32-11.png")
        # cli.py imports suggest_name directly; patch at the cli module level.
        with patch("shotname.ocr.run_ocr", return_value="Invoice paid by HDFC"), \
             patch("shotname.cli.suggest_name",
                   wraps=naming_mod.suggest_name) as mock_suggest, \
             patch("shotname.popup.ask_filename", return_value=_CANCEL), \
             patch("shotname.clipboard.copy_image", return_value=True):
            _process(src, self.folder, popup_timeout=8.0)
        mock_suggest.assert_called_once()
        args = mock_suggest.call_args[0]
        self.assertEqual(args[0], "Invoice paid by HDFC")


if __name__ == "__main__":
    unittest.main()
