import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, Mock
from feedparser import FeedParserDict as Entry
import main as app
import explainer
import japan_life

def entry(identity, title, summary="", content=None):
    return Entry(id=identity, title=title, summary=summary, content=content or [])

class PosterTests(unittest.TestCase):
    def test_rss_retries_transient_server_error(self):
        failed = Mock(status_code=500, content=b"")
        failed.raise_for_status.side_effect = app.requests.HTTPError("500")
        success = Mock(status_code=200, content=b"<rss><channel><item><title>Myanmar</title></item></channel></rss>")
        success.raise_for_status.return_value = None
        with patch.object(app.requests, "get", side_effect=[failed, success]) as get, patch.object(app.time, "sleep"):
            entries = app.fetch_rss_entries({"name": "test", "url": "https://example.com/feed"})
        self.assertEqual(get.call_count, 2)
        self.assertEqual(len(entries), 1)

    def test_article_text_falls_back_to_article_page(self):
        item = entry("one", "Myanmar news", "short")
        item.link = "https://example.com/article"
        html = "<html><nav>menu</nav><article>" + "".join(f"<p>word {i} details</p>" for i in range(60)) + "</article></html>"
        with patch.object(app, "fetch_article_html", return_value=html):
            text = app.get_entry_article_text(item)
        self.assertIn("word 59 details", text)
        self.assertNotIn("menu", text)

    def test_build_x_post_text_is_bounded_and_keeps_url(self):
        text = app.build_x_post_text("題" * 120, "https://example.com/post")
        title, url = text.splitlines()
        self.assertEqual(len(title), 100)
        self.assertTrue(title.endswith("…"))
        self.assertEqual(url, "https://example.com/post")

    def test_x_post_skips_without_credentials(self):
        with patch.dict(app.os.environ, {}, clear=True), patch.object(app.requests, "post") as post:
            self.assertIsNone(app.post_to_x_if_configured("Title", "https://example.com"))
            post.assert_not_called()

    def test_x_post_uses_api_and_returns_id(self):
        env = {
            "X_API_KEY": "key", "X_API_SECRET": "secret",
            "X_ACCESS_TOKEN": "token", "X_ACCESS_TOKEN_SECRET": "token-secret",
        }
        response = Mock()
        response.json.return_value = {"data": {"id": "123"}}
        with patch.dict(app.os.environ, env, clear=True), patch.object(app.requests, "post", return_value=response) as post:
            self.assertEqual(app.post_to_x_if_configured("Title", "https://example.com"), "123")
            self.assertEqual(post.call_args.kwargs["json"]["text"], "Title\nhttps://example.com")
            response.raise_for_status.assert_called_once()

    def test_gemini_retry_is_bounded(self):
        unavailable = Mock(status_code=503)
        unavailable.raise_for_status.side_effect = app.requests.HTTPError("503")
        success = Mock(status_code=200)
        success.json.return_value = {"candidates": [{"content": {"parts": [{"text": "OK"}]}}]}
        with patch.dict(app.os.environ, {"GEMINI_API_KEY": "test"}), patch.object(app.time, "sleep") as sleep:
            with patch.object(app.requests, "post", side_effect=[unavailable, success]) as post:
                self.assertEqual(app.call_gemini_generate_content("test"), "OK")
                self.assertEqual(post.call_count, 2)
            with patch.object(app.requests, "post", return_value=unavailable) as post:
                with self.assertRaises(app.requests.HTTPError):
                    app.call_gemini_generate_content("test")
                self.assertEqual(post.call_count, 3)
            forbidden = Mock(status_code=403)
            forbidden.raise_for_status.side_effect = app.requests.HTTPError("403")
            with patch.object(app.requests, "post", return_value=forbidden) as post:
                with self.assertRaises(app.requests.HTTPError):
                    app.call_gemini_generate_content("test")
                post.assert_called_once()

    def test_gemini_retries_transient_connection_failures(self):
        success = Mock(status_code=200)
        success.json.return_value = {"candidates": [{"content": {"parts": [{"text": "OK"}]}}]}
        with patch.dict(app.os.environ, {"GEMINI_API_KEY": "test"}), patch.object(app.time, "sleep") as sleep:
            with patch.object(
                app.requests,
                "post",
                side_effect=[app.requests.ReadTimeout("slow"), success],
            ) as post:
                self.assertEqual(app.call_gemini_generate_content("test"), "OK")
                self.assertEqual(post.call_count, 2)
                sleep.assert_called_once_with(10)

            with patch.object(app.requests, "post", side_effect=app.requests.ConnectionError("offline")) as post:
                with self.assertRaises(app.requests.ConnectionError):
                    app.call_gemini_generate_content("test")
                self.assertEqual(post.call_count, 3)

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
        with patch.object(app, "load_seen_ids", return_value=set()), patch.object(app, "fetch_rss_entries", return_value=[entry("x", "Myanmar", "Teaser")]), patch.object(app, "fetch_article_html", return_value=None), patch.object(app, "call_gemini_generate_content") as generate:
            with self.assertRaises(RuntimeError):
                app.main()
            generate.assert_not_called()


class ExplainerTests(unittest.TestCase):
    def test_used_topics_are_excluded(self):
        remaining = explainer.unused_topics([{"id": "pdf-basics", "published_at": "2026-09-20T00:00:00+00:00"}])
        self.assertNotIn("pdf-basics", {topic["id"] for topic in remaining})

    def test_recent_skip_has_cooldown_but_expired_skip_is_eligible(self):
        now = datetime(2026, 9, 22, tzinfo=timezone.utc)
        recent = [{
            "id": "nug-basics", "status": "skipped",
            "retry_after": (now + timedelta(days=1)).isoformat(),
        }]
        expired = [{
            "id": "nug-basics", "status": "skipped",
            "retry_after": (now - timedelta(days=1)).isoformat(),
        }]
        self.assertNotIn("nug-basics", {topic["id"] for topic in explainer.unused_topics(recent, now)})
        self.assertIn("nug-basics", {topic["id"] for topic in explainer.unused_topics(expired, now)})

    def test_unknown_model_choice_falls_back_to_first_unused(self):
        history = [{"id": "pdf-basics", "status": "published"}]
        with patch.object(explainer.news, "call_gemini_generate_content", return_value='{"id":"invented"}'):
            selected = explainer.select_topic(history, signals=[])
        self.assertEqual(selected["id"], explainer.unused_topics(history)[0]["id"])

    def test_research_requires_two_distinct_usable_sources(self):
        topic = {"id": "test", "query": "test", "title": "test"}
        candidates = [
            {"domain": "reuters.com", "url": "https://reuters.com/a", "title": "A"},
            {"domain": "reuters.com", "url": "https://reuters.com/b", "title": "B"},
        ]
        page = "<article><p>" + ("word " * 150) + "</p></article>"
        empty = Mock()
        empty.json.return_value = []
        empty.raise_for_status.return_value = None
        with patch.object(explainer.requests, "get", return_value=empty), patch.object(explainer, "gdelt_candidates", return_value=candidates), patch.object(explainer.news, "fetch_rss_entries", return_value=[]), patch.object(explainer.news, "fetch_article_html", return_value=page):
            with self.assertRaises(RuntimeError):
                explainer.fetch_research_sources(topic)

    def test_run_tries_next_topic_after_source_shortage(self):
        topics = [explainer.TOPICS[0], explainer.TOPICS[1]]
        sources = [
            {"publisher": "one.example", "title": "One", "url": "https://one.example/a", "excerpt": "a"},
            {"publisher": "two.example", "title": "Two", "url": "https://two.example/b", "excerpt": "b"},
        ]
        generated = "Title\n" + ("本文" * 500)
        with patch.object(explainer, "load_history", return_value=[]), patch.object(explainer, "topic_attempt_order", return_value=topics), patch.object(explainer, "fetch_research_sources", side_effect=[explainer.InsufficientSourcesError(topics[0]["id"], 1), sources]), patch.object(explainer.news, "call_gemini_generate_content", return_value=generated), patch.object(explainer.news, "post_to_hatena", return_value="https://example.com/post") as post, patch.object(explainer.news, "post_to_x_if_configured"), patch.object(explainer, "save_history") as save:
            result = explainer.run()
        self.assertEqual(result["id"], topics[1]["id"])
        post.assert_called_once()
        saved = save.call_args.args[0]
        self.assertEqual(saved[0]["status"], "skipped")
        self.assertEqual(saved[1]["status"], "published")

    def test_all_source_shortages_finish_successfully_and_record_cooldowns(self):
        topics = [explainer.TOPICS[0], explainer.TOPICS[1]]
        failures = [explainer.InsufficientSourcesError(topic["id"], 1) for topic in topics]
        with patch.object(explainer, "load_history", return_value=[]), patch.object(explainer, "topic_attempt_order", return_value=topics), patch.object(explainer, "fetch_research_sources", side_effect=failures), patch.object(explainer.news, "post_to_hatena") as post, patch.object(explainer, "save_history") as save:
            result = explainer.run()
        self.assertIsNone(result["article_url"])
        self.assertEqual(len(result["attempts"]), 2)
        post.assert_not_called()
        self.assertEqual(len(save.call_args.args[0]), 2)


class JapanLifeTests(unittest.TestCase):
    def test_used_life_topic_is_excluded(self):
        topic = japan_life.select_topic([{"id": "residence-card-loss"}])
        self.assertNotEqual(topic["id"], "residence-card-loss")

    def test_requested_unknown_or_used_topic_is_rejected(self):
        with self.assertRaises(RuntimeError):
            japan_life.select_topic([], "invented")
        with self.assertRaises(RuntimeError):
            japan_life.select_topic([{"id": "health-insurance"}], "health-insurance")

    def test_relevant_excerpt_prefers_keyword_section(self):
        text = ("unrelated text " * 300) + ("在留カード 紛失 再交付 警察 14日 " * 120)
        excerpt = japan_life._relevant_excerpt(text, ["在留カード", "紛失", "再交付"])
        self.assertIn("在留カード", excerpt)

    def test_article_validation_requires_myanmar_text_length_and_source(self):
        valid_body = "မြန်မာဘာသာ " * 100 + "\nhttps://www.moj.go.jp/"
        japan_life.validate_article("ဂျပန်တွင် နေထိုင်ခြင်း", valid_body)
        with self.assertRaises(RuntimeError):
            japan_life.validate_article("Japanese title", "x" * 1000 + " https://example.com")

    def test_dry_run_never_posts_or_changes_history(self):
        generated = "ဂျပန်တွင် နေထိုင်ခြင်း\n" + ("မြန်မာဘာသာ " * 100) + "\nhttps://www.moj.go.jp/"
        sources = [{"title": "Official", "url": "https://www.moj.go.jp/", "excerpt": "x" * 500}]
        with patch.object(japan_life, "load_history", return_value=[]), patch.object(japan_life, "collect_sources", return_value=sources), patch.object(japan_life.news, "call_gemini_generate_content", return_value=generated), patch.object(japan_life.news, "post_to_hatena") as post, patch.object(japan_life, "save_history") as save:
            result = japan_life.run(dry_run=True, topic_id="residence-card-loss")
        self.assertIsNone(result["article_url"])
        post.assert_not_called()
        save.assert_not_called()

if __name__ == "__main__":
    unittest.main()

