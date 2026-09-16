import unittest
from unittest.mock import patch
from feedparser import FeedParserDict as Entry
import main as app

def entry(identity, title, summary="", content=None):
    return Entry(id=identity, title=title, summary=summary, content=content or [])

class PosterTests(unittest.TestCase):
    def test_related_before_newer_world_news(self):
        world = entry("world", "Philippines election")
        related = entry("related", "Myanmar peace talks")
        self.assertEqual(app.choose_new_entry([world, related], set())[1], "related")
        self.assertEqual(app.choose_new_entry([world, related], {"related"})[1], "world")
        self.assertIsNone(app.choose_new_entry([world, related], {"world", "related"}))

    def test_stable_order_and_summary_matching(self):
        first = entry("one", "Regional talks", "<p>Envoys discuss Burma</p>")
        second = entry("two", "Myanmar news")
        self.assertEqual(app.choose_new_entry([first, second], set())[1], "one")
        self.assertFalse(app.is_myanmar_related(entry("x", "Burmania travel")))

    def test_body_uses_rss_content(self):
        item = entry("one", "News", "Short teaser", [{"value": "<p>Actual body</p><script>bad()</script>"}])
        self.assertEqual(app.rss_article_text(item), "Actual body")
        self.assertEqual(app.rss_article_text(entry("two", "News", "<p>Summary</p>")), "Summary")

    def test_failures_propagate_and_do_not_save(self):
        item = entry("one", "Myanmar news", "word " * 100)
        for stage in ("call_gemini_generate_content", "post_to_hatena"):
            with patch.object(app, "load_seen_ids", return_value=set()), patch.object(app, "fetch_rss_entries", return_value=[item]), patch.object(app, "call_gemini_generate_content", return_value="Title\nBody"), patch.object(app, "post_to_hatena"), patch.object(app, "save_seen_ids") as save:
                with patch.object(app, stage, side_effect=RuntimeError("failed")):
                    with self.assertRaises(RuntimeError):
                        app.main()
                save.assert_not_called()

    def test_rss_failure_does_not_skip_other_diagnostics(self):
        env = dict(GEMINI_API_KEY="test", HATENA_ID="test", HATENA_API_KEY="test", HATENA_BLOG_ID="test")
        with patch.dict(app.os.environ, env), patch.object(app, "fetch_rss_entries", side_effect=RuntimeError("RSS failed")), patch.object(app, "call_gemini_generate_content") as gemini, patch.object(app.requests, "get") as get, patch.object(app, "post_to_hatena") as post, patch.object(app, "save_seen_ids") as save:
            with self.assertRaises(RuntimeError):
                app.diagnose("ja")
            gemini.assert_called_once()
            get.assert_called_once()
            post.assert_not_called()
            save.assert_not_called()

    def test_short_body_never_generates(self):
        with patch.object(app, "load_seen_ids", return_value=set()), patch.object(app, "fetch_rss_entries", return_value=[entry("x", "Myanmar", "Teaser")]), patch.object(app, "call_gemini_generate_content") as generate:
            with self.assertRaises(RuntimeError):
                app.main()
            generate.assert_not_called()

if __name__ == "__main__":
    unittest.main()

