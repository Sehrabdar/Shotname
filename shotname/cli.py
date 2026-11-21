"""
shotname/cli.py
---------------
Entry point for shotname:  python -m shotname.cli

Full pipeline per screenshot
----------------------------
1. Extract timestamp (Ubuntu filename → st_mtime → now()).
2. Run OCR via shotname.ocr.
3. Generate suggested name via naming.suggest_name(ocr_text, timestamp).
4. Show editable popup (shotname.popup.ask_filename).
5a. Enter pressed → sanitize_user_name() → resolve_collision() → safe link/unlink rename.
5b. Esc / timeout → keep original filename.
6. Copy final (or original) path to clipboard.
"""

import argparse
import logging
import os
import re
import signal
import time
from datetime import datetime

from shotname import clipboard
from shotname import ocr as ocr_mod
from shotname import popup as popup_mod
from shotname.naming import resolve_collision, sanitize_user_name, suggest_name
from shotname.watcher import ScreenshotWatcher

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Screenshot filename pattern  "Screenshot from YYYY-MM-DD HH-MM-SS.png"
# ---------------------------------------------------------------------------
_TS_RE = re.compile(
    r"Screenshot from (\d{4})-(\d{2})-(\d{2}) (\d{2})-(\d{2})-(\d{2})\.png$"
)


# ---------------------------------------------------------------------------
# Timestamp extraction
# ---------------------------------------------------------------------------

def extract_timestamp(path: str) -> datetime:
    """
    Return the best available datetime for a screenshot at *path*.

    Priority
    --------
    1. Parse from the Ubuntu filename  "Screenshot from YYYY-MM-DD HH-MM-SS.png"
    2. File's ``st_mtime``
    3. ``datetime.now()``

    Note: birth-time is intentionally NOT used (unreliable on Linux).
    """
    name = os.path.basename(path)
    m = _TS_RE.match(name)
    if m:
        try:
            return datetime(
                int(m.group(1)), int(m.group(2)), int(m.group(3)),
                int(m.group(4)), int(m.group(5)), int(m.group(6)),
            )
        except ValueError:
            pass  # fall through

    try:
        mtime = os.stat(path).st_mtime
        return datetime.fromtimestamp(mtime)
    except OSError:
        pass

    return datetime.now()


# ---------------------------------------------------------------------------
# Safe rename (used by both timestamp-only and popup-confirmed paths)
# ---------------------------------------------------------------------------

def _do_rename(src: str, stem: str, folder: str) -> tuple[str, bool]:
    """
    Link *src* to *stem*.png (collision-resolved) inside *folder*, then unlink *src*.

    Returns (final_path, success).  On any failure returns (src, False) so the
    caller can still copy the original to the clipboard.
    """
    for _attempt in range(5):
        try:
            existing = set(os.listdir(folder))
        except OSError as exc:
            logger.error("Cannot list folder %s: %s", folder, exc)
            return src, False

        dst_name = resolve_collision(stem, existing, ext=".png")

        # Security: destination must stay inside folder
        dst = os.path.normpath(os.path.join(folder, dst_name))
        if not dst.startswith(os.path.normpath(folder) + os.sep):
            logger.error("Destination escapes folder: %s", dst)
            return src, False

        try:
            os.link(src, dst)
        except FileExistsError:
            logger.debug("Collision race on %s, retrying…", dst_name)
            continue
        except OSError as exc:
            logger.error("os.link failed for %s → %s: %s", src, dst, exc)
            return src, False

        try:
            os.unlink(src)
        except OSError as exc:
            logger.warning("os.unlink failed for %s: %s", src, exc)
            # dst exists and holds the data; treat as success.

        logger.info("Renamed: %s → %s", os.path.basename(src), dst_name)
        print(f"  ✔  renamed  {os.path.basename(src)}  →  {dst_name}")
        return dst, True

    logger.error("Gave up renaming %s after 5 collision retries.", src)
    return src, False


def safe_rename(src: str, folder: str) -> tuple[str, bool]:
    """Timestamp-only rename (used by old tests and as fallback)."""
    when = extract_timestamp(src)
    stem = when.strftime("%Y-%m-%d_%H-%M-%S")
    return _do_rename(src, stem, folder)


# ---------------------------------------------------------------------------
# Per-screenshot processor (watcher callback)
# ---------------------------------------------------------------------------

def _process(path: str, folder: str, popup_timeout: float) -> None:
    """
    Full per-screenshot pipeline.  Any unexpected exception is caught here
    so one broken screenshot cannot crash the watcher loop.
    """
    try:
        _process_inner(path, folder, popup_timeout)
    except Exception as exc:  # noqa: BLE001
        logger.error("Unhandled exception processing %s: %s", path, exc, exc_info=True)
        print(f"  ✘  unexpected error processing {os.path.basename(path)}: {exc}")
        # Best-effort clipboard copy of the original so the image is not lost
        try:
            clipboard.copy_image(path)
        except Exception:
            pass


def _process_inner(path: str, folder: str, popup_timeout: float) -> None:
    print(f"\n[shotname] detected: {os.path.basename(path)}")

    # 1. Timestamp
    when = extract_timestamp(path)

    # 2. OCR
    try:
        ocr_text = ocr_mod.run_ocr(path)
    except Exception as exc:        # keep the loop alive
        logger.error("Unexpected OCR exception for %s: %s", path, exc)
        ocr_text = ""

    # 3. Suggest name
    suggested = suggest_name(ocr_text, when)
    if ocr_text:
        print(f"  🔍 OCR preview: {ocr_text.splitlines()[0][:60]!r}")
    print(f"  💡 suggested:   {suggested}")

    # 4. Show popup
    try:
        popup_result = popup_mod.ask_filename(suggested, timeout=popup_timeout)
    except Exception as exc:
        logger.error("Popup error for %s: %s", path, exc)
        popup_result = popup_mod.PopupResult(action="cancel")

    # 5. Act on popup result
    final_path = path
    renamed = False

    if popup_result.accepted:
        raw_name = popup_result.name
        stem = sanitize_user_name(raw_name)

        if not stem:
            print("  ⚠  empty name after sanitization; keeping original.")
        else:
            final_path, renamed = _do_rename(path, stem, folder)
            if not renamed:
                print(f"  ⚠  rename failed; keeping: {os.path.basename(final_path)}")
    else:
        print("  ↩  cancelled (Esc/timeout); keeping original filename.")

    # 6. Clipboard – always, using whatever path we ended up with
    ok = clipboard.copy_image(final_path)
    if ok:
        print(f"  📋 clipboard ← {os.path.basename(final_path)}")
    else:
        print(f"  ⚠  clipboard copy failed for {os.path.basename(final_path)}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shotname",
        description="Auto-rename Ubuntu screenshots and copy them to the clipboard.",
    )
    parser.add_argument(
        "--folder",
        default="~/Pictures/Screenshots",
        help="Screenshots folder to watch (default: ~/Pictures/Screenshots)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=8.0,
        help="Popup timeout in seconds (default: 8)",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    folder = os.path.expanduser(args.folder)
    folder = os.path.abspath(folder)
    popup_timeout: float = args.timeout

    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    # --- Startup dependency checks ------------------------------------------
    if not ocr_mod.check_tesseract():
        print("[shotname] Tesseract OCR is not installed or not on PATH.")
        print("           Install it with:  sudo apt install tesseract-ocr")
        raise SystemExit(1)

    if not popup_mod.check_tkinter():
        print("[shotname] tkinter is not available.")
        print("           Install it with:  sudo apt install python3-tk")
        raise SystemExit(1)

    if not os.path.isdir(folder):
        print(f"[shotname] folder does not exist: {folder}")
        raise SystemExit(1)

    print(f"[shotname] watching {folder}  (popup timeout={popup_timeout}s, Ctrl-C to quit)")
    print(f"[shotname] session:  {clipboard.detect_session()}")

    processed: list[str] = []

    def on_screenshot(path: str) -> None:
        _process(path, folder, popup_timeout)
        processed.append(path)

    watcher = ScreenshotWatcher(folder, on_screenshot, poll_interval=0.5)

    def _sigint(_sig, _frame):
        print("\n[shotname] interrupted.")
        watcher.stop()

    signal.signal(signal.SIGINT, _sigint)

    watcher._running = True
    logger.info("Watcher started – folder=%s", folder)
    try:
        while watcher._running:
            watcher._tick()
            time.sleep(watcher.poll_interval)
    finally:
        logger.info("Watcher stopped.")
        print(f"[shotname] done. Processed {len(processed)} file(s).")


if __name__ == "__main__":
    main()
