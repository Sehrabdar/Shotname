# shotname

A small, local screenshot auto-naming utility for Ubuntu.

## The Annoyance

I take dozens of screenshots while working—capturing bug reports, payment confirmations, dashboard metrics, and code snippets. On Ubuntu, every capture lands in `~/Pictures/Screenshots` with a default name like:

`Screenshot from 2026-11-21 10-32-11.png`

Six weeks later, searching for a specific receipt or error modal by timestamp is hopeless. The standard manual routine was tedious: take the screenshot, open the file manager or terminal, locate the latest file, rename it to something descriptive, return to chat or email, and attach or paste the image.

`shotname` automates this sequence: it detects newly saved screenshots, extracts visible text using local OCR, suggests a clean slugified filename in a lightweight popup, safely renames the file upon confirmation, and copies the image directly to your system clipboard.

## Demo

> **Target Platform:** Ubuntu only (Wayland and X11).


https://github.com/user-attachments/assets/cc0dcede-55db-42ab-a2a4-6424ba93cfe2


## Key Decisions

- **Local-only processing:** Runs entirely on your machine. No screenshots or OCR text ever leave your system.
- **Zero paid APIs or LLM calls:** Uses standard Tesseract OCR rather than third-party cloud vision models. It is fast, free, private, and works offline.
- **Confirmation popup over silent renaming:** OCR can be imperfect or capture incidental text. A 50-pixel editable prompt allows quick editing with Enter, or cancellation with Esc/timeout.
- **Polling over inotify/watchdog:** A 0.5-second polling interval requires zero external C extensions or pip packages and avoids inotify descriptor exhaustion.
- **Python standard library only:** No pip requirements. Zero third-party Python dependencies to maintain.
- **Pure naming engine:** `shotname/naming.py` has no filesystem, GUI, or subprocess dependencies, ensuring deterministic slug generation and fast unit testing.
- **Atomic, collision-safe renaming:** Uses `os.link(src, dst)` followed by `os.unlink(src)` with automatic `-2`, `-3` numeric suffixes. Files are never overwritten or clobbered.
- **Native Wayland and X11 clipboard:** Dispatches to `wl-copy` or `xclip` depending on active session variables (`$XDG_SESSION_TYPE` / `$WAYLAND_DISPLAY`).

## What I Left Out and Why

To maintain reliability for a focused v1 build, several features were intentionally excluded:

- **Async OCR:** Synchronous execution keeps file stability and processing order strictly sequential and predictable.
- **Text-size ranking:** Word selection uses top-down line ordering and stopword filtering rather than geometric bounding-box heuristics.
- **Multi-OS support:** Tailored specifically for Ubuntu's default screenshot naming conventions and desktop sessions.
- **Settings UI:** Flags are configured straightforwardly via CLI arguments.
- **Background daemon / autostart:** Runs directly in the terminal under the user's control without systemd unit overhead.
- **Annotation or editing:** Dedicated screenshot tools (like Flameshot or GNOME Screenshot) already handle drawing; `shotname` focuses purely on naming and clipboard handoff.
- **Search or tagging UI:** Files are renamed to standard, searchable desktop filenames so existing OS file search tools (like GNOME Search, Nautilus, or `find`) work naturally.
- **Cloud sync or upload:** Keeps the application private, local, and credential-free.
- **Retroactive renaming:** Only newly captured screenshots are monitored, avoiding accidental mass-renaming of historic files.

## How to Run

### 1. Install System Dependencies

`shotname` relies on standard Ubuntu system packages:

```bash
sudo apt install tesseract-ocr xclip wl-clipboard python3-tk
```

- `tesseract-ocr`: Required at startup for text extraction.
- `python3-tk`: Required at startup for the confirmation popup.
- `wl-clipboard` / `xclip`: Required for Wayland and X11 clipboard integration respectively.

### 2. Launch the Application

```bash
python -m shotname.cli
```

### CLI Flags

- `--folder <path>`: Folder to watch for new screenshots (Default: `~/Pictures/Screenshots`).
- `--timeout <seconds>`: Popup auto-dismiss timeout in seconds (Default: `8`).

**What happens on launch:**
1. The app verifies that `tesseract` and `tkinter` are installed and exits with a clear installation command if either is missing.
2. It detects whether the current session is Wayland or X11.
3. It begins polling the designated folder every 0.5 seconds for files matching `Screenshot*.png`.
4. Once a file's size stabilizes, OCR runs and a confirmation dialog appears:
   - **Enter:** Renames the file to the confirmed name (e.g. `2026-11-21_payment-failed.png`).
   - **Esc or Timeout:** Leaves the original filename untouched.
5. In either case, the screenshot is copied to your clipboard ready to paste (`Ctrl+V`).

## AI Assistance Note

AI tools were used for code generation, test authoring, and documentation assistance during development.

## Project Overview

```text
shotname/
  __init__.py    # Package marker
  naming.py      # Pure naming engine: slugify, stopword filtering, collision resolution
  watcher.py     # Polling-based folder watcher with file-stability verification
  ocr.py         # Subprocess wrapper for the system Tesseract binary
  popup.py       # Tkinter confirmation popup with auto-dismiss timeout
  clipboard.py   # Wayland (wl-copy) and X11 (xclip) image clipboard handler
  cli.py         # Main CLI pipeline, argument parsing, and watcher loop

tests/
  test_naming.py     # Naming engine, slugification, and collision tests
  test_watcher.py    # Polling, file stability, and loop prevention tests
  test_cli.py        # CLI workflow, safe renaming, and clipboard tests
  test_ocr.py        # Tesseract execution and error fallback tests
  test_popup.py      # Popup dialog behavior, keybindings, and timeout tests
  test_hardening.py  # Edge-case resilience and failure isolation tests

README.md
```

## Testing

Run the test suite using Python's standard `unittest` runner:

```bash
python -m unittest discover tests -v
```

The test suite covers:
- OCR text slugification, length/word limits, Unicode accents, and stopword stripping.
- Timestamp-based fallback when OCR text is empty, blank, or symbol garbage.
- Polling file stability (monitoring consecutive equal-size checks before processing).
- Path traversal sanitization (e.g., `../../evil` stripped).
- Collision avoidance using atomic hard links (`os.link`) and non-clobbering suffixes (`-2`, `-3`).
- Missing dependency warnings and graceful error handling.

## Scope and Limitations

- **Ubuntu Only:** Tested and intended for Ubuntu Linux desktop environments.
- **Wayland and X11 Support:** Uses session detection to choose between `wl-copy` and `xclip`.
- **Local Tesseract OCR:** OCR accuracy depends on image clarity, font size, and contrast; low-contrast or illegible text falls back to timestamp-based names (`YYYY-MM-DD_HH-MM-SS`).
- **Polling-Based Detection:** Checks folder contents every 0.5 seconds, ensuring portable detection across various filesystems without inotify locks.
- **Desktop Focus Behavior:** On some Wayland compositors (such as GNOME Shell with Mutter), anti-focus-stealing rules may require clicking the popup once before it registers keystrokes.

