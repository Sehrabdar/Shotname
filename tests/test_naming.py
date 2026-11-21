"""
tests/test_naming.py
--------------------
Unit tests for shotname/naming.py.
Standard library unittest only – no pytest, no external packages.
"""

import unittest
from datetime import datetime

from shotname.naming import resolve_collision, sanitize_user_name, slugify, suggest_name

# ---------------------------------------------------------------------------
# Fixed datetime used throughout the tests so results are deterministic.
# ---------------------------------------------------------------------------
_WHEN = datetime(2026, 11, 21, 14, 5, 9)
_DATE = "2026-11-21"
_FALLBACK = "2026-11-21_14-05-09"


# ===========================================================================
# slugify()
# ===========================================================================
class TestSlugify(unittest.TestCase):

    def test_basic_phrase(self):
        self.assertEqual(slugify("Payment failed Razorpay"), "payment-failed-razorpay")

    def test_stopwords_removed(self):
        # "the", "a", "is", "in" are stopwords
        self.assertEqual(slugify("the cat is in the hat"), "cat-hat")

    def test_max_words(self):
        result = slugify("one two three four five six seven")
        parts = result.split("-")
        self.assertLessEqual(len(parts), 5)

    def test_max_len(self):
        long_text = "abcdefghij " * 20          # very long input
        self.assertLessEqual(len(slugify(long_text)), 50)

    def test_no_trailing_hyphen_after_truncation(self):
        long_text = "a" * 60                    # one giant word
        result = slugify(long_text)
        self.assertFalse(result.endswith("-"))

    def test_unicode_input(self):
        # Accented chars decompose and their base letter survives
        result = slugify("café résumé naïve")
        self.assertIn("cafe", result)
        self.assertIn("resume", result)

    def test_emoji_input(self):
        # Emoji contain no ASCII letters, so the slug should be empty or
        # contain only whatever ASCII survived decomposition.
        result = slugify("🚀🔥💥")
        self.assertEqual(result, "")

    def test_emoji_mixed_with_text(self):
        result = slugify("🚀 launch failed")
        self.assertIn("launch", result)
        self.assertIn("failed", result)
        self.assertNotIn("🚀", result)

    def test_symbol_heavy_garbage(self):
        result = slugify("!!!###$$$%%%^^^&&&***")
        self.assertEqual(result, "")

    def test_pure_whitespace(self):
        self.assertEqual(slugify("   "), "")

    def test_empty_string(self):
        self.assertEqual(slugify(""), "")

    def test_single_stopword(self):
        self.assertEqual(slugify("the"), "")

    def test_hyphen_separated_output(self):
        result = slugify("hello world")
        self.assertRegex(result, r"^[a-z0-9]+(-[a-z0-9]+)*$")

    def test_punctuation_stripped(self):
        result = slugify("Hello, World! How's it going?")
        self.assertNotIn(",", result)
        self.assertNotIn("!", result)
        self.assertNotIn("?", result)
        self.assertNotIn("'", result)

    def test_max_words_custom(self):
        result = slugify("one two three four five six", max_words=3)
        self.assertLessEqual(len(result.split("-")), 3)


# ===========================================================================
# suggest_name()
# ===========================================================================
class TestSuggestName(unittest.TestCase):

    def test_normal_ocr(self):
        result = suggest_name("Payment failed Razorpay", _WHEN)
        self.assertTrue(result.startswith(_DATE + "_"))
        self.assertIn("payment", result)
        self.assertIn("failed", result)
        self.assertIn("razorpay", result)

    def test_date_prefix_always_present(self):
        for text in ["hello world", "🔥", "", "   "]:
            result = suggest_name(text, _WHEN)
            self.assertTrue(result.startswith(_DATE),
                            f"Date prefix missing for input {text!r}: {result!r}")

    def test_empty_ocr_fallback(self):
        self.assertEqual(suggest_name("", _WHEN), _FALLBACK)

    def test_whitespace_only_ocr_fallback(self):
        self.assertEqual(suggest_name("   \n\t  ", _WHEN), _FALLBACK)

    def test_symbol_garbage_ocr_fallback(self):
        self.assertEqual(suggest_name("!!!###$$$", _WHEN), _FALLBACK)

    def test_emoji_only_fallback(self):
        self.assertEqual(suggest_name("🚀🔥💥", _WHEN), _FALLBACK)

    def test_unicode_ocr(self):
        result = suggest_name("Résumé uploaded", _WHEN)
        self.assertTrue(result.startswith(_DATE + "_"))
        self.assertIn("resume", result)

    def test_multiline_uses_first_meaningful_line(self):
        # First line is garbage, second is meaningful
        result = suggest_name("!!!###\nPayment confirmed", _WHEN)
        self.assertIn("payment", result)
        self.assertIn("confirmed", result)

    def test_long_ocr_limited(self):
        long_line = "one two three four five six seven eight nine ten"
        result = suggest_name(long_line, _WHEN)
        slug_part = result[len(_DATE) + 1:]      # strip "YYYY-MM-DD_"
        self.assertLessEqual(len(slug_part.split("-")), 5)
        self.assertLessEqual(len(result), len(_DATE) + 1 + 50)

    def test_fallback_uses_supplied_datetime_not_now(self):
        fixed = datetime(2000, 1, 1, 0, 0, 0)
        result = suggest_name("", fixed)
        self.assertEqual(result, "2000-01-01_00-00-00")

    def test_example_from_spec(self):
        """Spec example: 2026-11-21_payment-failed-razorpay"""
        when = datetime(2026, 11, 21, 10, 0, 0)
        result = suggest_name("Payment failed on Razorpay", when)
        self.assertTrue(result.startswith("2026-11-21_"))
        self.assertIn("payment", result)
        self.assertIn("failed", result)
        self.assertIn("razorpay", result)
        # "on" is a stopword and should be absent
        parts = result.split("_", 1)[1].split("-")
        self.assertNotIn("on", parts)


# ===========================================================================
# sanitize_user_name()
# ===========================================================================
class TestSanitizeUserName(unittest.TestCase):

    def test_plain_name(self):
        self.assertEqual(sanitize_user_name("my-screenshot"), "my-screenshot")

    def test_strips_whitespace(self):
        self.assertEqual(sanitize_user_name("  hello  "), "hello")

    def test_removes_png_extension(self):
        self.assertEqual(sanitize_user_name("invoice.png"), "invoice")

    def test_removes_jpg_extension(self):
        self.assertEqual(sanitize_user_name("photo.jpg"), "photo")

    def test_path_traversal_dotdot_slash(self):
        result = sanitize_user_name("../../evil")
        self.assertNotIn("/", result)
        self.assertNotIn("..", result)

    def test_path_separator_slash(self):
        result = sanitize_user_name("a/b")
        self.assertNotIn("/", result)

    def test_path_separator_backslash(self):
        result = sanitize_user_name("a\\b")
        self.assertNotIn("\\", result)

    def test_empty_string(self):
        self.assertEqual(sanitize_user_name(""), "")

    def test_only_dots(self):
        # "..." should collapse to empty after stripping leading/trailing dots
        result = sanitize_user_name("...")
        self.assertEqual(result, "")

    def test_only_extension(self):
        # ".png" with nothing before the dot → empty after stripping
        result = sanitize_user_name(".png")
        self.assertEqual(result, "")

    def test_no_filesystem_ops(self):
        """sanitize_user_name must not raise even on exotic inputs."""
        exotic_inputs = [
            "/etc/passwd",
            "../../../../root/.ssh/authorized_keys",
            "\x00null",
            "\nNewline",
            "CON",          # Windows reserved name (harmless on Linux)
        ]
        for inp in exotic_inputs:
            with self.subTest(inp=inp):
                result = sanitize_user_name(inp)
                self.assertNotIn("/", result)
                self.assertNotIn("\\", result)


# ===========================================================================
# resolve_collision()
# ===========================================================================
class TestResolveCollision(unittest.TestCase):

    def test_no_collision(self):
        self.assertEqual(resolve_collision("shot", set()), "shot.png")

    def test_first_collision_gives_dash_2(self):
        existing = {"shot.png"}
        self.assertEqual(resolve_collision("shot", existing), "shot-2.png")

    def test_second_collision_gives_dash_3(self):
        existing = {"shot.png", "shot-2.png"}
        self.assertEqual(resolve_collision("shot", existing), "shot-3.png")

    def test_many_collisions(self):
        existing = {f"shot.png"} | {f"shot-{i}.png" for i in range(2, 11)}
        result = resolve_collision("shot", existing)
        self.assertEqual(result, "shot-11.png")

    def test_does_not_mutate_existing_set(self):
        existing = {"shot.png"}
        original = set(existing)
        resolve_collision("shot", existing)
        self.assertEqual(existing, original)

    def test_custom_extension(self):
        result = resolve_collision("shot", set(), ext=".jpg")
        self.assertEqual(result, "shot.jpg")

    def test_custom_extension_collision(self):
        existing = {"shot.jpg"}
        result = resolve_collision("shot", existing, ext=".jpg")
        self.assertEqual(result, "shot-2.jpg")

    def test_user_edited_name_collision(self):
        """Simulate: user edits a name that already exists → gets -2 suffix."""
        existing = {"invoice-paid.png"}
        result = resolve_collision("invoice-paid", existing)
        self.assertEqual(result, "invoice-paid-2.png")

    def test_user_edited_name_multiple_collisions(self):
        existing = {"invoice-paid.png", "invoice-paid-2.png"}
        result = resolve_collision("invoice-paid", existing)
        self.assertEqual(result, "invoice-paid-3.png")


# ===========================================================================
# Integration smoke test
# ===========================================================================
class TestIntegration(unittest.TestCase):

    def test_full_pipeline_no_collision(self):
        name = suggest_name("Order confirmed by Swiggy", _WHEN)
        stem = name          # suggest_name returns stem only (no extension)
        final = resolve_collision(stem, set())
        self.assertTrue(final.endswith(".png"))
        self.assertIn("order", final)

    def test_full_pipeline_with_collision(self):
        name = suggest_name("Order confirmed by Swiggy", _WHEN)
        existing = {name + ".png"}
        final = resolve_collision(name, existing)
        self.assertTrue(final.endswith("-2.png"))

    def test_sanitize_then_resolve(self):
        raw = "my invoice.png"
        stem = sanitize_user_name(raw)       # → "my invoice"
        # slugify isn't called here, but the stem is clean enough
        final = resolve_collision(stem, set())
        self.assertNotIn("/", final)
        self.assertTrue(final.endswith(".png"))


if __name__ == "__main__":
    unittest.main()

