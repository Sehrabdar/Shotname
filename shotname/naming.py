"""
shotname/naming.py
------------------
Pure naming engine: no OCR, no GUI, no filesystem, no subprocess.
Standard library only.
"""

import re
import unicodedata
from datetime import datetime

# ---------------------------------------------------------------------------
# Stopwords – common English words that add no search value in a filename
# ---------------------------------------------------------------------------
_STOPWORDS = frozenset({
    "a", "an", "the",
    "and", "or", "but", "nor", "so", "yet", "for",
    "in", "on", "at", "to", "of", "by", "up", "as",
    "is", "it", "its", "be", "was", "are", "were",
    "i", "my", "we", "you", "he", "she", "they",
    "this", "that", "these", "those",
    "not", "no", "has", "have", "had",
    "with", "from", "into", "onto",
    "do", "did", "if", "then", "than",
    "s", "t", "re", "ve", "ll", "d",   # contraction remnants
})


def slugify(text: str, max_words: int = 5, max_len: int = 50) -> str:
    """
    Convert *text* into a hyphen-separated, filesystem-safe slug.

    Steps
    -----
    1. NFKD-normalise so accented characters decompose (é → e + combining).
    2. Keep only ASCII letters, digits, and whitespace; drop everything else.
    3. Lowercase.
    4. Split into words and remove stopwords.
    5. Keep at most *max_words* meaningful words.
    6. Join with hyphens and truncate to *max_len* characters (at a word
       boundary so we never leave a dangling hyphen).
    7. Return empty string if nothing meaningful remains.
    """
    if not isinstance(text, str):
        return ""

    # 1. Unicode normalise – decomposes accented chars, strips combining marks
    normalised = unicodedata.normalize("NFKD", text)

    # 2. Strip everything that is not an ASCII letter, digit, or whitespace.
    #    This safely discards emoji, punctuation, symbols, and other junk.
    ascii_only = normalised.encode("ascii", errors="ignore").decode("ascii")
    cleaned = re.sub(r"[^a-zA-Z0-9\s]", " ", ascii_only)

    # 3. Lowercase
    lower = cleaned.lower()

    # 4. Split and remove stopwords / single characters
    words = [w for w in lower.split() if w not in _STOPWORDS and len(w) > 1]

    if not words:
        return ""

    # 5. Limit word count
    words = words[:max_words]

    # 6. Join and enforce length cap (truncate at a word boundary)
    slug = "-".join(words)
    if len(slug) > max_len:
        slug = slug[:max_len]
        # Walk back to the last hyphen so we don't leave a partial word
        last_hyphen = slug.rfind("-")
        if last_hyphen > 0:
            slug = slug[:last_hyphen]
        # If there is no hyphen at all the single word is very long; just cut it
        slug = slug.rstrip("-")

    return slug


def suggest_name(ocr_text: str, when: datetime) -> str:
    """
    Suggest a screenshot filename based on *ocr_text* and the caller-supplied
    *when* datetime.

    Format
    ------
    - Normal:   ``YYYY-MM-DD_meaningful-words``
    - Fallback: ``YYYY-MM-DD_HH-MM-SS``  (when OCR yields nothing useful)

    The datetime is **always** taken from *when*; the filesystem is never
    consulted.
    """
    date_prefix = when.strftime("%Y-%m-%d")
    fallback = when.strftime("%Y-%m-%d_%H-%M-%S")

    if not ocr_text or not ocr_text.strip():
        return fallback

    # Try each line, top-to-bottom, until we get a usable slug
    for line in ocr_text.splitlines():
        slug = slugify(line.strip())
        if slug:
            return f"{date_prefix}_{slug}"

    return fallback


def sanitize_user_name(raw: str) -> str:
    """
    Sanitise a user-provided filename stem so it is safe to use on disk.

    Rules
    -----
    - Strip surrounding whitespace.
    - Remove any file extension (e.g. ``.png``).
    - Remove **all** path separators (``/`` and ``\\``) – this neutralises
      path-traversal attempts such as ``../../evil``.
    - Remove null bytes (``\\x00``) which are illegal in Linux filenames.
    - Return an empty string if nothing usable remains.
    - Never performs filesystem operations.
    """
    if not isinstance(raw, str):
        return ""

    value = raw.strip()

    # Remove null bytes – illegal in POSIX filenames
    value = value.replace("\x00", "")

    # Remove a trailing file extension (anything after the last dot)
    # Only strip common image extensions to avoid eating meaningful dots.
    dot_index = value.rfind(".")
    if dot_index != -1:
        ext = value[dot_index:].lower()
        if ext in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".webp"}:
            value = value[:dot_index]

    # Remove ALL path separators to prevent traversal (/, \, and sequences)
    value = re.sub(r"[/\\]", "", value)

    # Strip any residual dots that could become leading "." (hidden files /
    # traversal remnants like ".." after separators are removed)
    value = value.strip(".")

    # Collapse repeated hyphens/underscores and strip whitespace again
    value = value.strip()

    return value


def resolve_collision(stem: str, existing: set[str], ext: str = ".png") -> str:
    """
    Return a filename based on *stem* + *ext* that is **not** in *existing*.

    Strategy
    --------
    - Try ``stem.png`` first.
    - If taken, try ``stem-2.png``, ``stem-3.png``, … until a free slot
      is found.
    - *existing* is never modified.
    - No filesystem access is performed.
    - If *stem* is empty, uses ``"untitled"`` as a safe fallback.
    """
    # Guard against an empty stem producing a bare extension like ".png"
    safe_stem = stem if stem else "untitled"

    candidate = f"{safe_stem}{ext}"
    if candidate not in existing:
        return candidate

    counter = 2
    while True:
        candidate = f"{safe_stem}-{counter}{ext}"
        if candidate not in existing:
            return candidate
        counter += 1

