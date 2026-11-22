"""
shotname/watcher.py
-------------------
Polling-based screenshot watcher.

Public API
----------
ScreenshotWatcher(folder, callback, poll_interval, stability_checks,
                  open_retries, open_retry_delay)

    .run()   – Block forever, calling ``callback(path)`` for each stable
               new screenshot.  Runs in the calling thread; wrap in a
               Thread/daemon if you need a background watcher.

    .stop()  – Signal the run-loop to exit cleanly (safe to call from
               another thread or a signal handler).

    .handled – ``set[str]``: absolute paths already seen.  Can be
               pre-populated with filenames that have already been
               processed (e.g. restored from a previous session).

Design decisions
----------------
* Pure polling – no inotify, watchdog, or OS-specific event APIs.
* No threads or asyncio inside the module; the caller decides threading.
* File stability: two consecutive polls with identical st_size required.
* Open-access verification with configurable retry before giving up.
* Broken / vanishing files do NOT crash the loop; they are logged and skipped.
"""

import fnmatch
import logging
import os
import time

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (also exposed so tests can read / override them)
# ---------------------------------------------------------------------------
POLL_INTERVAL: float = 0.5          # seconds between directory scans
STABILITY_CHECKS: int = 2           # consecutive equal-size polls required
OPEN_RETRIES: int = 5               # max open() attempts after stability
OPEN_RETRY_DELAY: float = 0.3       # seconds between open() retries

_SCREENSHOT_PATTERN: str = "Screenshot*.png"


class ScreenshotWatcher:
    """
    Watch *folder* for new ``Screenshot*.png`` files and invoke *callback*
    with the absolute path of each stable file.

    Parameters
    ----------
    folder : str
        Absolute (or relative) path to the directory to watch.
    callback : callable[[str], None]
        Called with the absolute path of each newly stable screenshot.
        Exceptions raised by the callback are caught and logged so the
        watcher loop continues.
    poll_interval : float
        Seconds between full directory scans.  Default: 0.5.
    stability_checks : int
        How many consecutive equal-size polls are required before a file
        is considered fully written.  Default: 2.
    open_retries : int
        How many times to retry ``open()`` after a PermissionError or
        FileNotFoundError before giving up.  Default: 5.
    open_retry_delay : float
        Seconds to wait between ``open()`` retries.  Default: 0.3.
    """

    def __init__(
        self,
        folder: str,
        callback,
        poll_interval: float = POLL_INTERVAL,
        stability_checks: int = STABILITY_CHECKS,
        open_retries: int = OPEN_RETRIES,
        open_retry_delay: float = OPEN_RETRY_DELAY,
    ) -> None:
        self.folder = os.path.abspath(folder)
        self.callback = callback
        self.poll_interval = poll_interval
        self.stability_checks = stability_checks
        self.open_retries = open_retries
        self.open_retry_delay = open_retry_delay

        # Absolute paths that must not be processed again.
        self.handled: set[str] = set()

        # Tracks (size, consecutive_equal_count) for files currently being
        # monitored for stability.  Keyed by absolute path.
        self._pending: dict[str, tuple[int, int]] = {}

        self._running: bool = False

        # Pre-populate handled with every screenshot that already exists in the
        # folder so they are never treated as new files at startup.
        try:
            for _name in os.listdir(self.folder):
                if not _name.startswith(".") and fnmatch.fnmatch(_name, _SCREENSHOT_PATTERN):
                    self.handled.add(os.path.join(self.folder, _name))
        except OSError:
            pass  # Folder may not exist yet; _tick() will handle it gracefully.

    # ------------------------------------------------------------------
    # Public control
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Block, polling the folder until :meth:`stop` is called."""
        self._running = True
        logger.info("Watcher started – folder=%s  interval=%.1fs", self.folder, self.poll_interval)
        try:
            while self._running:
                self._tick()
                time.sleep(self.poll_interval)
        finally:
            logger.info("Watcher stopped.")

    def stop(self) -> None:
        """Signal the run-loop to exit after the current tick."""
        self._running = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        """One full scan: discover new files and advance stability checks."""
        try:
            entries = os.listdir(self.folder)
        except OSError as exc:
            logger.error("Cannot list folder %s: %s", self.folder, exc)
            return

        for name in entries:
            # 1. Skip dotfiles
            if name.startswith("."):
                continue

            # 2. Match the screenshot pattern (case-sensitive on Linux)
            if not fnmatch.fnmatch(name, _SCREENSHOT_PATTERN):
                continue

            abs_path = os.path.join(self.folder, name)

            # 3. Skip already-handled paths
            if abs_path in self.handled:
                continue

            # 4. Advance the stability state machine for this path
            self._advance_stability(abs_path)

    def _current_size(self, path: str) -> int | None:
        """
        Return the file size in bytes, or None if the file cannot be stat-ed
        (e.g. it was removed between listdir and stat).
        """
        try:
            return os.stat(path).st_size
        except (FileNotFoundError, PermissionError, OSError) as exc:
            logger.debug("stat failed for %s: %s", path, exc)
            return None

    def _advance_stability(self, path: str) -> None:
        """
        Progress the stability state machine for *path*.

        State stored in ``self._pending[path]``: ``(last_size, equal_count)``.
        When *equal_count* reaches ``self.stability_checks`` the file is
        considered stable and handed off.
        """
        size = self._current_size(path)
        if size is None:
            # File vanished – remove from pending, do not mark handled.
            self._pending.pop(path, None)
            logger.debug("File vanished before stable: %s", path)
            return

        last_size, equal_count = self._pending.get(path, (-1, 0))

        if size == last_size:
            equal_count += 1
        else:
            # Size changed (or first time seen); reset counter.
            equal_count = 1

        if equal_count >= self.stability_checks:
            # Stable – attempt to open and hand off.
            self._pending.pop(path, None)
            self._try_emit(path)
        else:
            self._pending[path] = (size, equal_count)
            logger.debug("Stability %d/%d for %s (size=%d)",
                         equal_count, self.stability_checks, path, size)

    def _try_emit(self, path: str) -> None:
        """
        Verify the file can be opened, then invoke the callback.

        Retries up to ``self.open_retries`` times on PermissionError /
        FileNotFoundError.  After exhausting retries the path is added to
        ``self.handled`` so it is never retried again.
        """
        for attempt in range(1, self.open_retries + 1):
            try:
                with open(path, "rb"):
                    pass
                # Opened successfully – hand off to the caller.
                logger.info("Stable file detected: %s", path)
                self.handled.add(path)
                try:
                    self.callback(path)
                except Exception as cb_exc:  # noqa: BLE001
                    logger.error("Callback raised for %s: %s", path, cb_exc)
                return
            except (PermissionError, FileNotFoundError) as exc:
                logger.warning(
                    "Cannot open %s (attempt %d/%d): %s",
                    path, attempt, self.open_retries, exc,
                )
                if attempt < self.open_retries:
                    time.sleep(self.open_retry_delay)
            except OSError as exc:
                # Unexpected OS error – give up immediately.
                logger.error("Unexpected OSError opening %s: %s", path, exc)
                break

        # Retries exhausted or unexpected error.
        logger.error("Giving up on %s after %d open attempts.", path, self.open_retries)
        self.handled.add(path)

