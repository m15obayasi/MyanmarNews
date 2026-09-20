import os
import json
import logging
import traceback
import html
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Set, Tuple

import requests
import feedparser
from bs4 import BeautifulSoup
import markdown
from requests_oauthlib import OAuth1


# ===============================
# ログ設定
# ===============================
logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(message)s",
)

# ===============================
# RSS ソース定義
# ===============================
RSS_SOURCES = [
    {
        "name": "DVB (English)",
        "url": "https://english.dvb.no/feed/",
        "lang": "en",
    },
    {
        "name": "Myanmar Now (English)",
        "url": "https://myanmar-now.org/en/feed/",
        "lang": "en",
    },
    {
        "name": "ReliefWeb Myanmar",
        "url": "https://reliefweb.int/updates/rss.xml?search=country.exact%3A%22Myanmar%22",
        "lang": "en",
    },
]

# 日本語版は既存ファイル名をそのまま利用
SEEN_FILE_JA = "seen_articles.json"
# 英語ブログ用に別ファイルを用意（初回は自動生成される）
SEEN_FILE_EN = "seen_articles_en.json"


# ===============================
# ユーティリティ
# ===============================
def load_seen_ids(path: str) -> Set[str]:
    """過去に投稿した記事 ID を読み込む"""
    if not os.path.exists(path):
        logging.info(f"[INFO] {path} not found. Creating new empty file.")
        save_seen_ids(path, set())
        return set()

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return set(data)
        elif isinstance(data, dict) and "ids" in data:
            return set(data["ids"])
        else:
            logging.warning(f"[WARN] {path} format unknown, resetting.")
            return set()
    except Exception as e:
        logging.warning(f"[WARN] Failed to load {path}: {e}")
        return set()


def save_seen_ids(path: str, ids: Set[str]) -> None:
    """投稿済み記事 ID を保存する"""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sorted(list(ids)), f, ensure_ascii=False, indent=2)
        logging.info(f"[INFO] {path} updated.")
    except Exception as e:
        logging.error(f"[ERROR] Failed to save {path}: {e}")
        raise


def fetch_rss_entries(source: Dict[str, Any]) -> List[Dict[str, Any]]:
    """RSS を取得して entries を返す"""
    url = source["url"]
    name = source["name"]
    logging.info(f"[INFO] Checking RSS source: {name} ({url})")

    response = None
    for attempt in range(3):
        response = requests.get(
            url,
            timeout=30,
            headers={"User-Agent": "MyanmarNewsBot/1.0 (+https://github.com/m15obayasi/MyanmarNews)"},
        )
        if response.status_code not in (429, 500, 502, 503, 504) or attempt == 2:
            break
        delay = 5 * (2 ** attempt)
        logging.warning("RSS temporarily unavailable (HTTP %s); retry in %ss", response.status_code, delay)
        time.sleep(delay)
    assert response is not None
    response.raise_for_status()
    feed = feedparser.parse(response.content)
    entries = getattr(feed, "entries", []) or []
    if not entries:
        detail = "invalid XML and no entries" if getattr(feed, "bozo", False) else "no entries"
        raise RuntimeError(f"RSS from {name} contains {detail}")
    if getattr(feed, "bozo", False):
        logging.warning("RSS from %s is not perfectly formed, but %s entries were recovered", name, len(entries))
    logging.info(f"[INFO] RSS fetched: {url} / entries = {len(entries)}")
    return entries


MYANMAR_TERMS = re.compile(
    r"\b(?:myanmar|burma|burmese|rohingya|yangon|rangoon|mandalay|"
    r"naypyidaw|naypyitaw|rakhine|kachin|karenni|sagaing|"
    r"aung san suu kyi|min aung hlaing)\b", re.IGNORECASE
)


def rss_article_text(entry: Any) -> str:
    """Use content:encoded when available; otherwise the published summary."""
    blocks = getattr(entry, "content", []) or []
    values = [block.get("value", "") for block in blocks]
    raw = "\n".join(value for value in values if value.strip())
    return html_to_text(raw or getattr(entry, "summary", ""))


def is_myanmar_related(entry: Any) -> bool:
    tags = getattr(entry, "tags", []) or []
    fields = [getattr(entry, "title", ""), getattr(entry, "summary", "")]
    fields.extend(tag.get("term", "") for tag in tags)
    # Avoid using publisher navigation/footer text as relevance evidence.
    return bool(MYANMAR_TERMS.search(html_to_text(" ".join(fields))))


def choose_new_entry(
    entries: List[Dict[str, Any]],
    seen_ids: Set[str],
) -> Optional[Tuple[Dict[str, Any], str]]:
    """まだ投稿していない記事を 1 本選ぶ"""

    # Stable ordering keeps the feed's newest-first order within each group.
    for e in sorted(entries, key=lambda item: not is_myanmar_related(item)):
        entry_id = getattr(e, "id", None) or getattr(e, "link", None)
        if not entry_id:
            # ID が無い時はタイトル＋リンクとかで擬似 ID を作る
            entry_id = (getattr(e, "title", "") + "|" + getattr(e, "link", "")).strip()

        if entry_id in seen_ids:
            continue
        return e, entry_id

    return None


def fetch_article_html(url: str) -> Optional[str]:
    """記事本体の HTML を取得（403 などは警告だけ出して None を返す）"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.text
    except requests.exceptions.HTTPError as e:
        logging.warning(f"[WARNING] Failed to fetch article content: {e}")
        return None
    except Exception as e:
        logging.warning(f"[WARNING] Error fetching article content: {e}")
        return None


def article_html_to_text(html_content: str) -> str:
    """Extract the article body when possible, avoiding site navigation text."""
    soup = BeautifulSoup(html_content, "lxml")
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "aside", "form"]):
        tag.decompose()
    container = soup.find("article") or soup.find("main")
    if container is None:
        container = soup.body or soup
    paragraphs = [p.get_text(" ", strip=True) for p in container.find_all(["p", "h2", "h3", "li"])]
    return "\n".join(line for line in paragraphs if line)


def get_entry_article_text(entry: Any) -> str:
    """Prefer RSS content, then fall back to the linked article body."""
    text = rss_article_text(entry)
    if len(text.split()) >= 80:
        return text
    link = getattr(entry, "link", "")
    if link:
        page = fetch_article_html(link)
        if page:
            extracted = article_html_to_text(page)
            if len(extracted.split()) > len(text.split()):
                return extracted
    return text


def html_to_text(html_content: str) -> str:
    """HTML からテキストをざっくり抽出"""
    soup = BeautifulSoup(html_content, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text(separator="\n")
    # 行を整形
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


# ===============================
# Gemini REST (v1beta) 呼び出し
# ===============================
def get_gemini_model_name() -> str:
    """環境変数からモデル名を取得（指定が無ければデフォルトを使う）"""
    env_model = os.getenv("GEMINI_MODEL")
    if env_model:
        logging.info(f"[INFO] Using Gemini model from env: {env_model}")
        return env_model

    # デフォルト（コンソールに表示される model name に合わせて適宜変更）
    default_model = "gemini-2.5-flash"
    logging.info(f"[INFO] GEMINI_MODEL not set. Using default: {default_model}")
    return default_model


def call_gemini_generate_content(prompt: str) -> str:
    """
    Gemini API (v1beta) を REST で叩いてテキストを返す。
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set in environment variables.")

    model = get_gemini_model_name()

    base_url = "https://generativelanguage.googleapis.com/v1beta"
    url = f"{base_url}/models/{model}:generateContent"

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ]
    }

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
    }

    logging.info(f"[INFO] Calling Gemini REST API (model={model}) ...")

    for attempt in range(3):
        resp = requests.post(url, headers=headers, json=payload, timeout=60)
        if resp.status_code not in (429, 500, 502, 503, 504) or attempt == 2:
            break
        delay = 10 * (2 ** attempt)
        logging.warning("Gemini temporarily unavailable (HTTP %s); retry in %ss", resp.status_code, delay)
        time.sleep(delay)
    try:
        resp.raise_for_status()
    except Exception:
        logging.error(f"[ERROR] Gemini HTTP error: {resp.status_code}")
        raise

    data = resp.json()
    candidates = data.get("candidates", [])
    if not candidates:
        raise RuntimeError("Gemini response has no candidates.")

    content = candidates[0].get("content", {})
    parts = content.get("parts", [])
    if not parts:
        raise RuntimeError("Gemini response has no parts in content.")

    text = "".join(part.get("text", "") for part in parts)
    if not text.strip():
        raise RuntimeError("Gemini response text is empty.")

    return text.strip()


def build_prompt_for_article(
    source_name: str,
    entry: Any,
    article_text: str,
    target_lang: str = "ja",
) -> str:
    """
    記事本文から、指定言語（日本語 / 英語）のブログ記事を生成するプロンプトを組み立てる。
    """

    title = getattr(entry, "title", "")
    link = getattr(entry, "link", "")
    summary = getattr(entry, "summary", "")

    # 記事本文が取れなかった場合は summary を使う
    base_text = article_text.strip() or summary.strip() or title

    if target_lang == "ja":
        prompt = f"""
あなたはミャンマー情勢に詳しい日本語ブロガーです。
以下の英語ニュース記事の内容をもとに、
日本語でわかりやすいブログ記事を書いてください。

# 制約・トーン
- 読者は「ミャンマーのことはある程度知っているが、現地ニュースを英語で追うのは大変」という日本の一般人を想定してください。
- 難しい政治用語は、できるだけ日本語で補足しながら説明してください。
- 陰謀論や極端な主張は避け、事実ベース＋穏やかな意見にとどめてください。
- 文字数はだいたい 1200〜2000 文字程度。
- 日本語のタイトルを 1 行目に書いてください。
- 2 行目以降は、以下のような構成で Markdown 形式で書いてください:
  - 導入：ニュースの概要を 2〜3 文で
  - 背景：なぜこの出来事が起きているのか
  - 今回のニュースのポイント：箇条書きでも可
  - ミャンマー市民や周辺国・国際社会への影響
  - 所感：主観的なコメントを最後に短く。見出しは必ず「所感」としてください。

# ニュースソース
- Source: {source_name}
- Original Title: {title}
- URL: {link}

# 英語記事本文（または要約）
{base_text}
""".strip()
    elif target_lang == "en":
        prompt = f"""
You are a blogger who is very familiar with politics and society in Myanmar.
Based on the following English news article, write an easy-to-understand blog post **in English**.

# Style & tone
- Target readers: people who care about Myanmar but do not have time to read every long news article.
- Explain complex political terms in simple language.
- Avoid conspiracy theories or extreme claims; focus on facts plus modest, balanced commentary.
- Length: around 800–1500 words.
- Write an English title on the first line.
- From the second line, write in Markdown with the following structure:
  - Introduction: 2–3 sentences summarizing the news
  - Background: why this event is happening
  - Key points of this news: bullet list is OK
  - Impact on Myanmar citizens, neighbouring countries, and the international community
  - Short personal comment as a blogger at the end

# News source
- Source: {source_name}
- Original Title: {title}
- URL: {link}

# Article body (or summary)
{base_text}
""".strip()
    else:
        raise ValueError(f"Unsupported target_lang: {target_lang}")

    return prompt


def split_title_and_body_from_gemini(text: str) -> Tuple[str, str]:
    """
    Gemini の出力からタイトル＋本文をざっくり分離。
    - 1 行目をタイトル
    - 2 行目以降を本文として扱う
    """
    lines = text.splitlines()
    if not lines:
        return "Myanmar News", text

    title = lines[0].strip().lstrip("#").strip()  # 先頭に # が付いていれば削る
    body = "\n".join(lines[1:]).strip()
    if not body:
        body = title
    return title, body


# ===============================
# はてなブログ投稿
# ===============================
def post_to_hatena(
    title: str,
    body_md: str,
    source_link: str,
    blog_id_env: str = "HATENA_BLOG_ID",
    categories: Optional[List[str]] = None,
) -> Optional[str]:
    """
    はてなブログに記事を投稿する（AtomPub）。
    content は HTML として送る。
    blog_id_env で使用する環境変数名を指定する（例: HATENA_BLOG_ID, HATENA_BLOG_ID_EN）。
    """
    hatena_id = os.getenv("HATENA_ID")
    api_key = os.getenv("HATENA_API_KEY")
    blog_id = os.getenv(blog_id_env)

    if not hatena_id or not api_key or not blog_id:
        raise RuntimeError(f"HATENA_ID / HATENA_API_KEY / {blog_id_env} が設定されていません。")

    endpoint = f"https://blog.hatena.ne.jp/{hatena_id}/{blog_id}/atom/entry"

    # Markdown → HTML
    body_html = markdown.markdown(body_md)

    # 元記事リンクを最後に付与
    if source_link:
        body_html += f'<hr><p>Source: <a href="{html.escape(source_link)}">{html.escape(source_link)}</a></p>'

    updated = datetime.now(timezone.utc).isoformat()

    category_xml = "\n  ".join(
        f'<category term="{html.escape(category, quote=True)}" />'
        for category in (categories or [])
    )
    if category_xml:
        category_xml = "\n  " + category_xml

    entry_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<entry xmlns="http://www.w3.org/2005/Atom"
       xmlns:app="http://www.w3.org/2007/app">
  <title>{html.escape(title)}</title>
  <author><name>{html.escape(hatena_id)}</name></author>
  <content type="text/html">{html.escape(body_html)}</content>
  <updated>{updated}</updated>{category_xml}
  <app:control>
    <app:draft>no</app:draft>
  </app:control>
</entry>
""".strip()

    auth = (hatena_id, api_key)
    headers = {"Content-Type": "application/xml"}

    logging.info(f"[INFO] Posting article to Hatena Blog ({blog_id_env}={blog_id}) ...")
    resp = requests.post(endpoint, data=entry_xml.encode("utf-8"), headers=headers, auth=auth, timeout=30)
    try:
        resp.raise_for_status()
    except Exception:
        logging.error(f"[ERROR] Hatena Blog post failed: {resp.status_code} {resp.text}")
        raise

    logging.info("[INFO] Hatena Blog post success.")

    location = resp.headers.get("Location")
    if location:
        return location
    try:
        root = ET.fromstring(resp.text)
        for link in root.findall("{http://www.w3.org/2005/Atom}link"):
            if link.get("rel") == "alternate" and link.get("href"):
                return link.get("href")
    except ET.ParseError:
        pass
    logging.warning("[WARNING] Hatena response did not include the published article URL; skipping X post.")
    return None


def build_x_post_text(title: str, article_url: str) -> str:
    """Build a conservatively sized X post containing the article title and URL."""
    clean_title = " ".join(title.split())
    if len(clean_title) > 100:
        clean_title = clean_title[:99].rstrip() + "…"
    return f"{clean_title}\n{article_url}"


def post_to_x_if_configured(title: str, article_url: str) -> Optional[str]:
    """Post to X using OAuth 1.0a. Skip cleanly until all credentials are configured."""
    names = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")
    credentials = {name: os.getenv(name) for name in names}
    configured = [name for name, value in credentials.items() if value]
    if not configured:
        logging.info("[INFO] X credentials are not configured; skipping X post.")
        return None
    missing = [name for name, value in credentials.items() if not value]
    if missing:
        raise RuntimeError("Incomplete X credentials: missing " + ", ".join(missing))

    auth = OAuth1(
        credentials["X_API_KEY"],
        credentials["X_API_SECRET"],
        credentials["X_ACCESS_TOKEN"],
        credentials["X_ACCESS_TOKEN_SECRET"],
    )
    response = requests.post(
        "https://api.x.com/2/tweets",
        json={"text": build_x_post_text(title, article_url)},
        auth=auth,
        timeout=30,
    )
    response.raise_for_status()
    post_id = response.json().get("data", {}).get("id")
    if not post_id:
        raise RuntimeError("X API response did not include a post ID")
    logging.info("[INFO] X post success (id=%s).", post_id)
    return post_id


# ===============================
# メイン処理
# ===============================
def diagnose(target_lang: str) -> None:
    """Check services without posting or modifying seen article records."""
    blog_env = "HATENA_BLOG_ID_EN" if target_lang == "en" else "HATENA_BLOG_ID"
    for name in ("GEMINI_API_KEY", "HATENA_ID", "HATENA_API_KEY", blog_env):
        if not os.getenv(name):
            raise RuntimeError(f"Missing environment variable: {name}")
    failures = []
    def check(name, operation):
        try:
            operation()
            logging.info("DIAGNOSTIC %s: OK", name)
        except Exception as exc:
            response = getattr(exc, "response", None)
            status = getattr(response, "status_code", None)
            logging.error("DIAGNOSTIC %s: FAILED (%s, HTTP %s)", name, type(exc).__name__, status)
            if name == "Gemini" and response is not None:
                try:
                    error = response.json().get("error", {})
                    logging.error("Gemini status: %s; message: %s", error.get("status"), error.get("message"))
                except ValueError:
                    pass
            failures.append(name)
    for source in RSS_SOURCES:
        def check_feed():
            entries = fetch_rss_entries(source)
            related = [entry for entry in entries if is_myanmar_related(entry)]
            logging.info("RSS entries: %s; Myanmar-related: %s", len(entries), len(related))
            selected = choose_new_entry(entries, set())
            if selected:
                words = len(rss_article_text(selected[0]).split())
                logging.info("Diagnostic selected title: %s; article words: %s",
                             getattr(selected[0], "title", ""), words)
                if words < 80:
                    raise RuntimeError("Selected RSS article body is too short")
        check("RSS", check_feed)
    check("Gemini", lambda: call_gemini_generate_content("Reply with OK only."))
    def check_hatena():
        endpoint = f"https://blog.hatena.ne.jp/{os.environ['HATENA_ID']}/{os.environ[blog_env]}/atom/entry"
        response = requests.get(endpoint, auth=(os.environ["HATENA_ID"], os.environ["HATENA_API_KEY"]), timeout=30)
        response.raise_for_status()
    check("Hatena " + target_lang, check_hatena)
    if failures:
        raise RuntimeError("Diagnostics failed: " + ", ".join(failures))
    logging.info("Diagnostics passed. No article posted.")


def main(target_lang: str = "ja") -> None:
    logging.info(f"==== Myanmar News Auto Poster (lang={target_lang}) ====")

    # 言語ごとに別の seen ファイルを使う（日本語版は従来の seen_articles.json を継続利用）
    if target_lang == "en":
        seen_file = SEEN_FILE_EN
        blog_id_env = "HATENA_BLOG_ID_EN"
    else:
        seen_file = SEEN_FILE_JA
        blog_id_env = "HATENA_BLOG_ID"

    seen_ids = load_seen_ids(seen_file)

    selected_entry = None
    selected_entry_id = None
    selected_source = None

    # 1. RSS から未読記事を 1 本選ぶ
    source_failures = []
    for source in RSS_SOURCES:
        try:
            entries = fetch_rss_entries(source)
        except Exception as exc:
            source_failures.append(f"{source['name']}: {exc}")
            logging.warning("[WARNING] Skipping unavailable RSS source %s: %s", source["name"], exc)
            continue
        result = choose_new_entry(entries, seen_ids)
        if result is None:
            continue
        entry, entry_id = result
        selected_entry = entry
        selected_entry_id = entry_id
        selected_source = source
        break

    if not selected_entry:
        if len(source_failures) == len(RSS_SOURCES):
            raise RuntimeError("All RSS sources failed: " + "; ".join(source_failures))
        logging.info("[INFO] No new articles found in all RSS sources. Exit.")
        return

    logging.info(
        f"[INFO] Selected new article from {selected_source['name']}: "
        f"{getattr(selected_entry, 'title', '')}"
    )

    # DVB publishes article content in RSS; avoid site navigation and paywalls.
    article_text = get_entry_article_text(selected_entry)
    if len(article_text.split()) < 80:
        raise RuntimeError("RSS article text is too short for reliable generation")
    logging.info("Myanmar-related selection: %s; RSS article words: %s",
                 is_myanmar_related(selected_entry), len(article_text.split()))

    # 3. Gemini に記事を生成してもらう（target_lang に応じて日本語/英語を切り替え）
    prompt = build_prompt_for_article(
        source_name=selected_source["name"],
        entry=selected_entry,
        article_text=article_text,
        target_lang=target_lang,
    )

    try:
        logging.info(f"[INFO] Generating article with Gemini (REST v1beta, lang={target_lang})...")
        gemini_output = call_gemini_generate_content(prompt)
    except Exception as e:
        logging.error(f"[ERROR] Gemini article generation failed: {e}")
        logging.error(traceback.format_exc())
        logging.error("[ERROR] Gemini failed to generate article. Exit without posting.")
        raise

    title, body_md = split_title_and_body_from_gemini(gemini_output)

    # 4. はてなブログに投稿
    try:
        article_url = post_to_hatena(title, body_md, getattr(selected_entry, "link", ""), blog_id_env=blog_id_env)
    except Exception as e:
        logging.error(f"[ERROR] Failed to post to Hatena Blog: {e}")
        logging.error(traceback.format_exc())
        raise

    # 5. 投稿済み ID を保存
    if selected_entry_id:
        seen_ids.add(selected_entry_id)
        save_seen_ids(seen_file, seen_ids)

    # Persist the article first so an X failure cannot cause a duplicate blog post.
    if target_lang == "ja" and article_url:
        post_to_x_if_configured(title, article_url)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Myanmar News Auto Poster")
    parser.add_argument(
        "--lang",
        choices=["ja", "en"],
        default="ja",
        help="Target language / blog ('ja' for Japanese blog, 'en' for English blog)",
    )
    parser.add_argument("--diagnose", action="store_true", help="Check services without publishing (uses one small Gemini request)")
    args = parser.parse_args()

    if args.diagnose:
        diagnose(args.lang)
    else:
        main(target_lang=args.lang)


