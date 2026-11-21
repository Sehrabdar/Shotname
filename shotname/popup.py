"""
shotname/popup.py
-----------------
Tkinter-based confirmation popup for screenshot renaming.

Public API
----------
PopupResult
    Named result returned by ask_filename().
    Fields: action ("accept"|"cancel"), name (str).

ask_filename(suggested: str, timeout: float = 8.0) -> PopupResult
    Show a small always-on-top dialog pre-filled with *suggested*.
    - Enter  → PopupResult(action="accept", name=<edited text>)
    - Esc    → PopupResult(action="cancel", name="")
    - Timeout → PopupResult(action="cancel", name="")

check_tkinter() -> bool
    Return True if tkinter is importable; False otherwise.

Design notes
------------
* tkinter is imported lazily inside ask_filename() so the rest of the
  application can import popup.py even on a system where python3-tk is not
  yet installed.
* The popup blocks the calling thread (mainloop).  cli.py calls it from the
  main thread so there is no concurrency issue.
* Always-on-top + focus_force() cover both X11 and Wayland (via XWayland).
  On pure Wayland compositors that honour _NET_WM_STATE_ABOVE inconsistently
  the user may need to click the window; this is documented in WAYLAND_NOTES.
* A single `_closed` flag prevents both the timeout callback and the Enter/
  Esc handlers from trying to destroy the window more than once.
"""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

WAYLAND_NOTES = """
Wayland focus note
------------------
Some Wayland compositors (e.g. GNOME ≥ 42 with Mutter) restrict which windows
can steal keyboard focus.  If the popup does not receive key events:
  1. Click the popup window once to give it focus.
  2. Then press Enter or Esc.
The always-on-top flag and focus_force() are applied but may be ignored by the
compositor.  This is a Wayland protocol limitation, not a bug in shotname.
"""


@dataclass
class PopupResult:
    """Return value of :func:`ask_filename`."""
    action: str          # "accept" | "cancel"
    name: str = field(default="")

    @property
    def accepted(self) -> bool:
        return self.action == "accept"


def check_tkinter() -> bool:
    """Return True if the tkinter module can be imported."""
    try:
        import tkinter  # noqa: F401
        return True
    except ImportError:
        return False


def ask_filename(suggested: str, timeout: float = 8.0) -> PopupResult:
    """
    Show an editable popup pre-filled with *suggested*.

    Parameters
    ----------
    suggested : str
        The OCR-generated filename stem shown in the entry box.
    timeout : float
        Seconds before the popup auto-dismisses (cancel).

    Returns
    -------
    PopupResult
        ``action="accept"`` with the (possibly edited) name, or
        ``action="cancel"`` with an empty name.
    """
    try:
        import tkinter as tk
        from tkinter import font as tkfont
    except ImportError:
        logger.error(
            "tkinter is not available. Install with: sudo apt install python3-tk"
        )
        return PopupResult(action="cancel")

def ask_filename(suggested: str, timeout: float = 8.0) -> PopupResult:
    """
    Show an editable popup pre-filled with *suggested*.

    Parameters
    ----------
    suggested : str
        The OCR-generated filename stem shown in the entry box.
    timeout : float
        Seconds before the popup auto-dismisses (cancel).

    Returns
    -------
    PopupResult
        ``action="accept"`` with the (possibly edited) name, or
        ``action="cancel"`` with an empty name.
    """
    try:
        import tkinter as tk
        from tkinter import font as tkfont
    except ImportError:
        logger.error(
            "tkinter is not available. Install with: sudo apt install python3-tk"
        )
        return PopupResult(action="cancel")

    # Sanitise inputs so the UI never receives None / non-string values
    if not isinstance(suggested, str):
        suggested = ""
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        timeout = 8.0

    result: PopupResult = PopupResult(action="cancel")
    _closed = [False]        # mutable flag visible to nested callbacks

    try:
        root = tk.Tk()
    except Exception as exc:   # TclError if no display, or other init failure
        logger.error("Failed to create tkinter window: %s", exc)
        return PopupResult(action="cancel")

    try:
        root.title("shotname – confirm filename")
        root.resizable(False, False)

        # Always on top; request focus immediately.
        root.attributes("-topmost", True)

        # --- Layout ---------------------------------------------------------
        pad = {"padx": 10, "pady": 6}

        lbl_top = tk.Label(root, text="Confirm or edit the filename:", anchor="w")
        lbl_top.pack(fill="x", **pad)

        entry_var = tk.StringVar(value=suggested)
        entry = tk.Entry(root, textvariable=entry_var, width=52,
                         font=tkfont.Font(size=11))
        entry.pack(fill="x", padx=10, pady=(0, 4))
        # Select all so the user can start typing immediately
        entry.select_range(0, "end")
        entry.icursor("end")

        lbl_hint = tk.Label(
            root,
            text=f"Enter = accept  •  Esc = cancel  •  auto-cancel in {int(timeout)}s",
            fg="gray50",
            font=tkfont.Font(size=9),
            anchor="w",
        )
        lbl_hint.pack(fill="x", padx=10, pady=(0, 8))

        btn_frame = tk.Frame(root)
        btn_frame.pack(fill="x", padx=10, pady=(0, 10))

        # --- Close helpers --------------------------------------------------
        def _safe_destroy() -> None:
            """Destroy the window, tolerating already-destroyed state."""
            try:
                root.destroy()
            except Exception:   # TclError if already destroyed
                pass

        def _close_cancel() -> None:
            if _closed[0]:
                return
            _closed[0] = True
            # result stays at the default cancel
            _safe_destroy()

        def _close_accept() -> None:
            nonlocal result
            if _closed[0]:
                return
            _closed[0] = True
            try:
                name = entry_var.get()
            except Exception:
                name = ""
            result = PopupResult(action="accept", name=name)
            _safe_destroy()

        # --- Buttons --------------------------------------------------------
        btn_ok = tk.Button(btn_frame, text="✔ Accept (Enter)",
                           command=_close_accept, width=18)
        btn_ok.pack(side="left", padx=(0, 6))

        btn_cancel = tk.Button(btn_frame, text="✘ Cancel (Esc)",
                               command=_close_cancel, width=16)
        btn_cancel.pack(side="left")

        # --- Key bindings ---------------------------------------------------
        root.bind("<Return>", lambda _e: _close_accept())
        root.bind("<KP_Enter>", lambda _e: _close_accept())   # numpad Enter
        root.bind("<Escape>", lambda _e: _close_cancel())

        # --- Timeout --------------------------------------------------------
        timeout_ms = max(1, int(timeout * 1000))
        root.after(timeout_ms, _close_cancel)

        # --- Centre on screen -----------------------------------------------
        root.update_idletasks()
        w, h = root.winfo_reqwidth(), root.winfo_reqheight()
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

        # Request focus after the event loop starts.
        root.after(50, root.focus_force)
        root.after(60, entry.focus_force)

        # Raise the window on compositors that honour it.
        root.lift()
        root.after(80, root.lift)

        root.mainloop()

    except Exception as exc:   # Catch any unexpected tkinter error mid-run
        logger.error("Unexpected popup error: %s", exc)
        # Attempt a clean shutdown; ignore double-destroy errors
        try:
            root.destroy()
        except Exception:
            pass

    return result

