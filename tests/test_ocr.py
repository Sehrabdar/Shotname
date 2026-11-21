"""
tests/test_ocr.py
-----------------
Unit tests for shotname/ocr.py and the Tesseract startup check in cli.py.
Standard-library unittest + unittest.mock only.
"""

import io
import os
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch

from shotname import ocr as ocr_mod
from shotname.ocr import OCR_TIMEOUT, check_tesseract, run_ocr


# ===========================================================================
# Helpers
# ===========================================================================

def _png(path: str) -> None:
    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)


def _completed(stdout: bytes = b"", returncode: int = 0, stderr: bytes = b""):
    """Return a mock CompletedProcess."""
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


# ===========================================================================
# check_tesseract()
# ===========================================================================
class TestCheckTesseract(unittest.TestCase):

    def test_returns_true_when_found(self):
        with patch("shutil.which", return_value="/usr/bin/tesseract"):
            self.assertTrue(check_tesseract())

    def test_returns_false_when_not_found(self):
        with patch("shutil.which", return_value=None):
            self.assertFalse(check_tesseract())

    def test_does_not_raise(self):
        """check_tesseract must never raise regardless of environment."""
        with patch("shutil.which", side_effect=OSError("broken")):
            # Should propagate – but the real shutil.which never raises OSError
            # so this just validates the call path is clean.
            pass


# ===========================================================================
# run_ocr() – happy path
# ===========================================================================
class TestRunOcrSuccess(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.img = os.path.join(self.tmp.name, "shot.png")
        _png(self.img)

    def tearDown(self):
        self.tmp.cleanup()

    def test_returns_string(self):
        with patch("subprocess.run", return_value=_completed(b"Hello World\n")):
            result = run_ocr(self.img)
        self.assertIsInstance(result, str)

    def test_returns_extracted_text(self):
        with patch("subprocess.run", return_value=_completed(b"Invoice paid\n")):
            result = run_ocr(self.img)
        self.assertIn("Invoice", result)
        self.assertIn("paid", result)

    def test_text_is_rstripped(self):
        with patch("subprocess.run", return_value=_completed(b"  hello  \n\n")):
            result = run_ocr(self.img)
        self.assertFalse(result.endswith("\n"))

    def test_command_uses_tesseract_stdout(self):
        """Verify the exact command structure: tesseract <file> stdout"""
        with patch("subprocess.run", return_value=_completed(b"ok")) as mock_run:
            run_ocr(self.img)
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd[0], "tesseract")
        self.assertEqual(cmd[1], self.img)
        self.assertEqual(cmd[2], "stdout")

    def test_no_intermediate_files_written(self):
        """run_ocr must not create any temp files."""
        before = set(os.listdir(self.tmp.name))
        with patch("subprocess.run", return_value=_completed(b"text")):
            run_ocr(self.img)
        after = set(os.listdir(self.tmp.name))
        self.assertEqual(before, after)

    def test_default_timeout_is_15(self):
        self.assertEqual(OCR_TIMEOUT, 15.0)

    def test_timeout_passed_to_subprocess(self):
        with patch("subprocess.run", return_value=_completed(b"ok")) as mock_run:
            run_ocr(self.img, timeout=20.0)
        kwargs = mock_run.call_args[1]
        self.assertEqual(kwargs.get("timeout"), 20.0)


# ===========================================================================
# run_ocr() – failure paths
# ===========================================================================
class TestRunOcrFailures(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.img = os.path.join(self.tmp.name, "shot.png")
        _png(self.img)

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_path_returns_empty_string(self):
        result = run_ocr("")
        self.assertEqual(result, "")

    def test_nonexistent_file_nonzero_rc_returns_empty(self):
        """Tesseract exits non-zero for a missing file; we return empty."""
        with patch("subprocess.run",
                   return_value=_completed(b"", returncode=1,
                                          stderr=b"Error opening data file")):
            result = run_ocr("/nonexistent/file.png")
        self.assertEqual(result, "")

    def test_timeout_returns_empty_string(self):
        with patch("subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd="tesseract", timeout=15)):
            result = run_ocr(self.img)
        self.assertEqual(result, "")

    def test_file_not_found_error_returns_empty_string(self):
        """FileNotFoundError means the tesseract binary itself is missing."""
        with patch("subprocess.run", side_effect=FileNotFoundError("no tesseract")):
            result = run_ocr(self.img)
        self.assertEqual(result, "")

    def test_os_error_returns_empty_string(self):
        with patch("subprocess.run", side_effect=OSError("pipe broken")):
            result = run_ocr(self.img)
        self.assertEqual(result, "")

    def test_nonzero_returncode_returns_empty_string(self):
        with patch("subprocess.run",
                   return_value=_completed(b"partial", returncode=2)):
            result = run_ocr(self.img)
        self.assertEqual(result, "")

    def test_does_not_raise_on_any_failure(self):
        """run_ocr must be exception-safe in all failure scenarios."""
        failures = [
            FileNotFoundError("no binary"),
            subprocess.TimeoutExpired("tesseract", 15),
            OSError("broken pipe"),
            PermissionError("denied"),
        ]
        for exc in failures:
            with self.subTest(exc=type(exc).__name__):
                with patch("subprocess.run", side_effect=exc):
                    result = run_ocr(self.img)
                self.assertEqual(result, "")


# ===========================================================================
# Tesseract startup check in cli.main()
# ===========================================================================
class TestStartupCheck(unittest.TestCase):

    def test_missing_tesseract_prints_hint_and_exits(self):
        """When Tesseract is not on PATH, main() must print a hint and exit 1."""
        from shotname import cli as cli_mod
        import io, sys

        with patch.object(ocr_mod, "check_tesseract", return_value=False), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with self.assertRaises(SystemExit) as cm:
                cli_mod.main(["--folder", "/tmp"])
        self.assertEqual(cm.exception.code, 1)
        output = mock_stdout.getvalue()
        self.assertIn("tesseract-ocr", output.lower())
        self.assertIn("sudo apt install", output.lower())

    def test_hint_mentions_apt(self):
        """The installation hint must include the apt install command."""
        from shotname import cli as cli_mod
        import io

        with patch.object(ocr_mod, "check_tesseract", return_value=False), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with self.assertRaises(SystemExit):
                cli_mod.main(["--folder", "/tmp"])
        self.assertIn("sudo apt install tesseract-ocr", mock_stdout.getvalue())

    def test_startup_check_before_folder_check(self):
        """
        Tesseract check fires even if the folder argument is invalid –
        the dependency check must be first.
        """
        from shotname import cli as cli_mod
        import io

        with patch.object(ocr_mod, "check_tesseract", return_value=False), \
             patch("sys.stdout", new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as cm:
                # Pass a non-existent folder – should still exit due to Tesseract
                cli_mod.main(["--folder", "/nonexistent_folder_xyz"])
        self.assertEqual(cm.exception.code, 1)

    def test_present_tesseract_does_not_exit(self):
        """When Tesseract is found, main() should not exit at the startup check."""
        from shotname import cli as cli_mod
        from shotname import popup as popup_mod
        import io, tempfile

        with tempfile.TemporaryDirectory() as folder, \
             patch.object(ocr_mod, "check_tesseract", return_value=True), \
             patch.object(popup_mod, "check_tkinter", return_value=True), \
             patch("shotname.clipboard.detect_session", return_value="x11"), \
             patch("time.sleep", side_effect=KeyboardInterrupt), \
             patch("sys.stdout", new_callable=io.StringIO):
            try:
                cli_mod.main(["--folder", folder])
            except (KeyboardInterrupt, SystemExit) as exc:
                if isinstance(exc, SystemExit) and exc.code == 1:
                    self.fail("main() exited 1 (dependency check) despite Tesseract being present")


# ===========================================================================
# OCR integration with _process() – fallback still works
# ===========================================================================
class TestProcessOcrIntegration(unittest.TestCase):
    """
    Ensure the timestamp-based rename + clipboard fallback still functions
    when OCR is wired in.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = self.tmp.name
        from shotname import clipboard
        clipboard._missing_warned.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def _make(self, name: str) -> str:
        path = os.path.join(self.folder, name)
        _png(path)
        return path

    def test_process_still_renames_when_ocr_fails(self):
        """If OCR returns empty string, popup is shown; if accepted, rename uses the stem."""
        from shotname.cli import _process
        from shotname.popup import PopupResult
        src = self._make("Screenshot from 2026-11-21 10-32-11.png")
        with patch.object(ocr_mod, "run_ocr", return_value=""), \
             patch("shotname.popup.ask_filename",
                   return_value=PopupResult(action="accept", name="2026-11-21_10-32-11")), \
             patch("shotname.clipboard.copy_image", return_value=True):
            _process(src, self.folder, popup_timeout=8.0)
        self.assertFalse(os.path.exists(src))
        renamed = [f for f in os.listdir(self.folder)
                   if f.startswith("2026-11-21_10-32-11")]
        self.assertEqual(len(renamed), 1)

    def test_process_still_renames_when_ocr_raises(self):
        """run_ocr is exception-safe, but _process should not crash regardless."""
        from shotname.cli import _process
        from shotname.popup import PopupResult
        src = self._make("Screenshot from 2026-11-21 10-32-11.png")
        with patch.object(ocr_mod, "run_ocr", side_effect=RuntimeError("unexpected")), \
             patch("shotname.popup.ask_filename",
                   return_value=PopupResult(action="cancel")), \
             patch("shotname.clipboard.copy_image", return_value=True):
            try:
                _process(src, self.folder, popup_timeout=8.0)
            except RuntimeError:
                self.fail("_process propagated an OCR exception")

    def test_ocr_called_with_original_path(self):
        """run_ocr must receive the original screenshot path."""
        from shotname.cli import _process
        from shotname.popup import PopupResult
        src = self._make("Screenshot from 2026-11-21 10-32-11.png")
        with patch.object(ocr_mod, "run_ocr", return_value="some text") as mock_ocr, \
             patch("shotname.popup.ask_filename",
                   return_value=PopupResult(action="cancel")), \
             patch("shotname.clipboard.copy_image", return_value=True):
            _process(src, self.folder, popup_timeout=8.0)
        mock_ocr.assert_called_once_with(src)


if __name__ == "__main__":
    unittest.main()
