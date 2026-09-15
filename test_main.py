import ast
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

# Execute application definitions with mocked external libraries: no API or posts.
tree = ast.parse(Path(__file__).with_name("main.py").read_text(encoding="utf-8"))
tree.body = [n for n in tree.body if not (
    isinstance(n, ast.Import) and any(a.name in ("requests", "feedparser", "markdown") for a in n.names)
    or isinstance(n, ast.ImportFrom) and n.module == "bs4"
)]
app = types.ModuleType("poster")
app.__dict__.update(requests=Mock(), feedparser=Mock(), markdown=Mock(), BeautifulSoup=Mock())
exec(compile(tree, "main.py", "exec"), app.__dict__)

class PosterTests(unittest.TestCase):
    def test_http_failure_is_not_no_news(self):
        app.requests.get.return_value.raise_for_status.side_effect = RuntimeError("503")
        with self.assertRaises(RuntimeError):
            app.fetch_rss_entries(app.RSS_SOURCES[0])
        app.requests.get.return_value.raise_for_status.side_effect = None

    def test_invalid_and_empty_feed_fail(self):
        for feed in (types.SimpleNamespace(bozo=True), types.SimpleNamespace(bozo=False, entries=[])):
            app.feedparser.parse.return_value = feed
            with self.assertRaises(RuntimeError):
                app.fetch_rss_entries(app.RSS_SOURCES[0])

    def test_generation_and_post_failures_propagate(self):
        entry = types.SimpleNamespace(id="new", title="News", link="", summary="Summary")
        for stage in ("call_gemini_generate_content", "post_to_hatena"):
            with patch.object(app, "load_seen_ids", return_value=set()), patch.object(app, "fetch_rss_entries", return_value=[entry]), patch.object(app, "call_gemini_generate_content", return_value="Title\nBody"), patch.object(app, "post_to_hatena"), patch.object(app, "save_seen_ids") as save:
                with patch.object(app, stage, side_effect=RuntimeError("failed")):
                    with self.assertRaises(RuntimeError):
                        app.main()
                save.assert_not_called()

    def test_diagnosis_never_posts_or_saves(self):
        env = dict(GEMINI_API_KEY="test", HATENA_ID="test", HATENA_API_KEY="test", HATENA_BLOG_ID="test")
        with patch.dict(app.os.environ, env), patch.object(app, "fetch_rss_entries"), patch.object(app, "call_gemini_generate_content"), patch.object(app, "post_to_hatena") as post, patch.object(app, "save_seen_ids") as save:
            app.diagnose("ja")
            post.assert_not_called()
            save.assert_not_called()

if __name__ == "__main__":
    unittest.main()

