"""
shotname/ocr.py
---------------
Thin wrapper around the system Tesseract executable.

Public API
----------
run_ocr(image_path: str, timeout: float = 15.0) -> str
    Run Tesseract on *image_path* and return the extracted text.
    Returns an empty string on any failure (missing file, Tesseract error,
    timeout).  Never raises.

check_tesseract() -> bool
    Return True if the tesseract binary is on PATH; False otherwise.
    Does NOT print anything – call site decides how to surface the error.

Design decisions
----------------
* ``tesseract <file> stdout`` writes to stdout; we capture that directly,
  no intermediate temp files.
* subprocess.run() is fine here because we consume the whole output once
  and do not need streaming; the timeout kwarg handles stuck processes.
* Raw text only – filename suggestion is the caller's job (naming.py).
"""

import logging
import shutil
import subprocess

logger = logging.getLogger(__name__)

_TESSERACT = "tesseract"
OCR_TIMEOUT: float = 15.0        # seconds before we give up on a single image


def check_tesseract() -> bool:
    """Return True if the tesseract binary is available on PATH."""
    return shutil.which(_TESSERACT) is not None


def run_ocr(image_path: str, timeout: float = OCR_TIMEOUT) -> str:
    """
    Run ``tesseract <image_path> stdout`` and return the captured text.

    Parameters
    ----------
    image_path : str
        Absolute (or relative) path to the PNG screenshot.
    timeout : float
        Maximum seconds to wait for Tesseract.  Default: 15.

    Returns
    -------
    str
        Extracted text, stripped of trailing whitespace.
        Empty string on any failure (Tesseract crash, timeout, missing file,
        permission error).
    """
    if not image_path:
        logger.debug("run_ocr: empty image_path supplied.")
        return ""

    cmd = [_TESSERACT, image_path, "stdout"]
    logger.debug("OCR command: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        # Tesseract binary itself not found on PATH.
        logger.error("Tesseract binary not found. Install with: sudo apt install tesseract-ocr")
        return ""
    except subprocess.TimeoutExpired:
        logger.warning("OCR timed out after %.1fs for: %s", timeout, image_path)
        return ""
    except OSError as exc:
        logger.error("OSError running Tesseract for %s: %s", image_path, exc)
        return ""

    if result.returncode != 0:
        stderr_snippet = result.stderr.decode(errors="replace").strip()[:200]
        logger.warning(
            "Tesseract returned rc=%d for %s. stderr: %s",
            result.returncode, image_path, stderr_snippet,
        )
        return ""

    text = result.stdout.decode(errors="replace").rstrip()
    logger.debug("OCR produced %d chars for %s", len(text), image_path)
    return text

