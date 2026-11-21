"""
tests/test_popup.py
-------------------
Unit tests for shotname/popup.py.

Strategy
--------
tkinter is fully mocked — no display needed, no python3-tk required.
We test:
  - PopupResult dataclass properties.
  - check_tkinter() with both import outcomes.
  - ask_filename() accept / cancel / timeout paths via callback injection.
  - That the entry is pre-filled with the suggested name.
  - That Enter calls _close_accept and Esc calls _close_cancel.
  - That the timeout fires _close_cancel.
  - That closing the window multiple times is safe (_closed flag).
  - That a missing tkinter returns a cancel result gracefully.
"""

import sys
import types
import unittest
from unittest.mock import MagicMock, call, patch


# ---------------------------------------------------------------------------
# Always importable regardless of whether python3-tk is installed
# ---------------------------------------------------------------------------
from shotname.popup import PopupResult, ask_filename, check_tkinter


# ===========================================================================
# PopupResult
# ===========================================================================
class TestPopupResult(unittest.TestCase):

    def test_accepted_true_when_accept(self):
        r = PopupResult(action="accept", name="my-file")
        self.assertTrue(r.accepted)

    def test_accepted_false_when_cancel(self):
        r = PopupResult(action="cancel")
        self.assertFalse(r.accepted)

    def test_default_name_is_empty(self):
        r = PopupResult(action="cancel")
        self.assertEqual(r.name, "")

    def test_name_preserved(self):
        r = PopupResult(action="accept", name="invoice-paid")
        self.assertEqual(r.name, "invoice-paid")


# ===========================================================================
# check_tkinter()
# ===========================================================================
class TestCheckTkinter(unittest.TestCase):

    def test_returns_true_when_importable(self):
        # Temporarily inject a fake tkinter if real one is missing
        fake_tk = types.ModuleType("tkinter")
        with patch.dict(sys.modules, {"tkinter": fake_tk}):
            self.assertTrue(check_tkinter())

    def test_returns_false_when_not_importable(self):
        with patch.dict(sys.modules, {"tkinter": None}):
            self.assertFalse(check_tkinter())


# ===========================================================================
# ask_filename() – full mock of tkinter
# ===========================================================================

def _build_tk_mock():
    """
    Return a mock tkinter module that records interactions and captures
    the callbacks registered via root.bind() and root.after().
    """
    tk_mod = MagicMock(name="tkinter")
    font_mod = MagicMock(name="tkinter.font")

    root = MagicMock(name="Tk()")
    root.winfo_reqwidth.return_value = 400
    root.winfo_reqheight.return_value = 150
    root.winfo_screenwidth.return_value = 1920
    root.winfo_screenheight.return_value = 1080

    # Capture after() callbacks so tests can fire them
    after_callbacks: dict[int, callable] = {}

    def _after(ms, cb=None, *args):
        if cb is not None:
            after_callbacks[ms] = cb
        return "after_id"

    root.after.side_effect = _after

    # Capture bind() callbacks
    bindings: dict[str, callable] = {}

    def _bind(seq, cb):
        bindings[seq] = cb

    root.bind.side_effect = _bind

    # StringVar tracks value
    entry_var = MagicMock(name="StringVar()")
    entry_var.get.return_value = "pre-filled-name"

    tk_mod.Tk.return_value = root
    tk_mod.StringVar.return_value = entry_var
    tk_mod.Label.return_value = MagicMock()
    tk_mod.Entry.return_value = MagicMock()
    tk_mod.Frame.return_value = MagicMock()
    tk_mod.Button.return_value = MagicMock()

    return tk_mod, font_mod, root, entry_var, after_callbacks, bindings


class TestAskFilenameAccept(unittest.TestCase):

    def test_enter_returns_accept_with_name(self):
        """Simulating Enter: fire the <Return> binding → accept result."""
        tk_mod, font_mod, root, entry_var, after_cbs, bindings = _build_tk_mock()
        entry_var.get.return_value = "my-edited-name"

        def _mainloop_fire_enter():
            # Fire the Return binding as if the user pressed Enter
            bindings["<Return>"](None)

        root.mainloop.side_effect = _mainloop_fire_enter

        with patch.dict(sys.modules, {"tkinter": tk_mod, "tkinter.font": font_mod}):
            result = ask_filename("my-edited-name", timeout=8.0)

        self.assertEqual(result.action, "accept")
        self.assertEqual(result.name, "my-edited-name")
        self.assertTrue(result.accepted)

    def test_kp_enter_also_accepts(self):
        """Numpad Enter should also accept."""
        tk_mod, font_mod, root, entry_var, after_cbs, bindings = _build_tk_mock()
        entry_var.get.return_value = "numpad-name"

        def _mainloop():
            bindings["<KP_Enter>"](None)

        root.mainloop.side_effect = _mainloop

        with patch.dict(sys.modules, {"tkinter": tk_mod, "tkinter.font": font_mod}):
            result = ask_filename("numpad-name", timeout=8.0)

        self.assertTrue(result.accepted)
        self.assertEqual(result.name, "numpad-name")


class TestAskFilenameCancel(unittest.TestCase):

    def test_esc_returns_cancel(self):
        tk_mod, font_mod, root, entry_var, after_cbs, bindings = _build_tk_mock()

        def _mainloop():
            bindings["<Escape>"](None)

        root.mainloop.side_effect = _mainloop

        with patch.dict(sys.modules, {"tkinter": tk_mod, "tkinter.font": font_mod}):
            result = ask_filename("suggested", timeout=8.0)

        self.assertEqual(result.action, "cancel")
        self.assertFalse(result.accepted)
        self.assertEqual(result.name, "")

    def test_timeout_returns_cancel(self):
        """Firing the timeout after() callback must cancel."""
        tk_mod, font_mod, root, entry_var, after_cbs, bindings = _build_tk_mock()

        def _mainloop():
            # Find the timeout callback (the one registered with the longest ms)
            timeout_key = max(after_cbs.keys())
            after_cbs[timeout_key]()   # fire timeout

        root.mainloop.side_effect = _mainloop

        with patch.dict(sys.modules, {"tkinter": tk_mod, "tkinter.font": font_mod}):
            result = ask_filename("suggested", timeout=5.0)

        self.assertEqual(result.action, "cancel")

    def test_double_close_does_not_raise(self):
        """
        Firing both Enter and Esc (or timeout + Esc) must not raise.
        The _closed flag prevents duplicate destroy() calls.
        """
        tk_mod, font_mod, root, entry_var, after_cbs, bindings = _build_tk_mock()

        def _mainloop():
            bindings["<Return>"](None)   # first close
            bindings["<Escape>"](None)   # second close – must be a no-op

        root.mainloop.side_effect = _mainloop

        with patch.dict(sys.modules, {"tkinter": tk_mod, "tkinter.font": font_mod}):
            result = ask_filename("x", timeout=8.0)

        # destroy called exactly once
        root.destroy.assert_called_once()
        self.assertTrue(result.accepted)   # first action wins


class TestAskFilenameEntryPrefill(unittest.TestCase):

    def test_entry_prefilled_with_suggested(self):
        """StringVar is initialised with the suggested name."""
        tk_mod, font_mod, root, entry_var, after_cbs, bindings = _build_tk_mock()
        root.mainloop.side_effect = lambda: bindings["<Escape>"](None)

        with patch.dict(sys.modules, {"tkinter": tk_mod, "tkinter.font": font_mod}):
            ask_filename("pre-filled-name", timeout=8.0)

        # StringVar must have been constructed with value="pre-filled-name"
        init_calls = tk_mod.StringVar.call_args_list
        self.assertTrue(
            any("pre-filled-name" in str(c) for c in init_calls),
            f"StringVar not initialised with suggested name. Calls: {init_calls}",
        )

    def test_always_on_top_set(self):
        tk_mod, font_mod, root, entry_var, after_cbs, bindings = _build_tk_mock()
        root.mainloop.side_effect = lambda: bindings["<Escape>"](None)

        with patch.dict(sys.modules, {"tkinter": tk_mod, "tkinter.font": font_mod}):
            ask_filename("x", timeout=8.0)

        root.attributes.assert_any_call("-topmost", True)

    def test_focus_force_scheduled(self):
        """focus_force must be scheduled via after() on the root."""
        tk_mod, font_mod, root, entry_var, after_cbs, bindings = _build_tk_mock()
        root.mainloop.side_effect = lambda: bindings["<Escape>"](None)

        with patch.dict(sys.modules, {"tkinter": tk_mod, "tkinter.font": font_mod}):
            ask_filename("x", timeout=8.0)

        # At least one after() call must have been registered
        self.assertTrue(root.after.called)


class TestAskFilenameMissingTkinter(unittest.TestCase):

    def test_returns_cancel_when_tkinter_missing(self):
        """If tkinter cannot be imported, ask_filename returns cancel gracefully."""
        with patch.dict(sys.modules, {"tkinter": None, "tkinter.font": None}):
            result = ask_filename("suggested", timeout=8.0)
        self.assertEqual(result.action, "cancel")
        self.assertFalse(result.accepted)


# ===========================================================================
# Startup check in cli.main() – tkinter dependency
# ===========================================================================
class TestTkinterStartupCheck(unittest.TestCase):

    def test_missing_tkinter_exits_with_hint(self):
        """main() must exit 1 and print an install hint if tkinter is absent."""
        import io
        from shotname import cli as cli_mod
        from shotname import popup as popup_mod
        from shotname import ocr as ocr_mod

        with patch.object(ocr_mod, "check_tesseract", return_value=True), \
             patch.object(popup_mod, "check_tkinter", return_value=False), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            with self.assertRaises(SystemExit) as cm:
                cli_mod.main(["--folder", "/tmp"])
        self.assertEqual(cm.exception.code, 1)
        output = mock_out.getvalue()
        self.assertIn("python3-tk", output)
        self.assertIn("sudo apt install", output)

    def test_present_tkinter_does_not_exit(self):
        """When both Tesseract and tkinter are available, no premature exit."""
        import io, tempfile
        from shotname import cli as cli_mod
        from shotname import popup as popup_mod
        from shotname import ocr as ocr_mod

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
                    self.fail("main() exited 1 (dependency check) unexpectedly")


if __name__ == "__main__":
    unittest.main()

