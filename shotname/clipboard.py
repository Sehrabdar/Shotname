"""
shotname/clipboard.py
---------------------
Clipboard integration for Ubuntu (Wayland + X11).

Public API
----------
copy_image(path: str) -> bool
    Copy the PNG image at *path* to the system clipboard.
    Returns True on success, False on failure (the error is logged/printed).

detect_session() -> str
    Return "wayland", "x11", or "unknown".

Both functions are thin wrappers; they never touch the filesystem except to
read the image file when copying.
"""

import logging
import os
import shutil
import subprocess

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sentinel so the "tool missing" hint is only printed once per process run.
# ---------------------------------------------------------------------------
_missing_warned: set[str] = set()


def detect_session() -> str:
    """
    Detect the desktop session type.

    Priority
    --------
    1. ``$XDG_SESSION_TYPE``  ("wayland" or "x11")
    2. ``$WAYLAND_DISPLAY``   (non-empty → wayland)
    3. "unknown"
    """
    xdg = os.environ.get("XDG_SESSION_TYPE", "").strip().lower()
    if xdg in {"wayland", "x11"}:
        return xdg
    if os.environ.get("WAYLAND_DISPLAY", "").strip():
        return "wayland"
    return "unknown"


def _warn_missing(tool: str, hint: str) -> None:
    """Print an installation hint, but only once per tool per process."""
    if tool not in _missing_warned:
        _missing_warned.add(tool)
        print(f"[shotname] clipboard: '{tool}' not found. {hint}")


def copy_image(path: str) -> bool:
    """
    Copy the PNG image at *path* to the system clipboard.

    Session detection
    -----------------
    - wayland → ``wl-copy --type image/png < file``
    - x11     → ``xclip -selection clipboard -t image/png -i file``
    - unknown → try x11 fallback, then wayland fallback

    Returns True if the clipboard tool was launched successfully.
    For X11, xclip must stay alive to serve clipboard contents, so we use
    Popen (not run/check_output).
    """
    session = detect_session()

    if session == "wayland":
        return _copy_wayland(path)
    if session == "x11":
        return _copy_x11(path)

    # Unknown session – try x11 first (most common on Ubuntu), then wayland.
    logger.warning("Session type unknown; trying x11 clipboard first.")
    if _copy_x11(path, _silent_missing=True):
        return True
    return _copy_wayland(path)


def _copy_wayland(path: str, _silent_missing: bool = False) -> bool:
    tool = "wl-copy"
    if not shutil.which(tool):
        if not _silent_missing:
            _warn_missing(
                tool,
                "Install with: sudo apt install wl-clipboard",
            )
        return False

    try:
        with open(path, "rb") as fh:
            proc = subprocess.run(
                [tool, "--type", "image/png"],
                stdin=fh,
                capture_output=True,
            )
        if proc.returncode != 0:
            logger.error("wl-copy failed (rc=%d): %s", proc.returncode,
                         proc.stderr.decode(errors="replace"))
            return False
        logger.info("Clipboard (wayland): %s", path)
        return True
    except FileNotFoundError as exc:
        logger.error("Image file not found for wl-copy: %s – %s", path, exc)
        return False
    except (OSError, subprocess.SubprocessError) as exc:
        logger.error("wl-copy error for %s: %s", path, exc)
        return False


def _copy_x11(path: str, _silent_missing: bool = False) -> bool:
    tool = "xclip"
    if not shutil.which(tool):
        if not _silent_missing:
            _warn_missing(
                tool,
                "Install with: sudo apt install xclip",
            )
        return False

    try:
        # xclip must stay alive to serve the selection; use Popen, not run().
        subprocess.Popen(
            [tool, "-selection", "clipboard", "-t", "image/png", "-i", path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("Clipboard (x11/xclip): %s", path)
        return True
    except (OSError, subprocess.SubprocessError) as exc:
        logger.error("xclip error for %s: %s", path, exc)
        return False

