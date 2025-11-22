import os
import json
import requests
import feedparser
from bs4 import BeautifulSoup
from google import genai
import markdown
import re

SEEN_FILE = "seen_articles.json"

# --------------------------------------------------
# seen_articles.json 読み込み
# --------------------------------------------------
def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen), f, ensure_ascii=False, indent=2)


# --------------------------------------------------
# RSS取得（Irrawaddy は charset 壊れているので UTF-8 に強制変換）
# --------------------------------------------------
def fetch_rss_raw(url):
    """feedparser が壊れるので自前で取得→UTF-8 に張り替え"""
    r = requests.get(url, timeout=10)
    r.encoding = "utf-8"  # ここで強制 UTF-8
    return feedparser.parse(r.text)


def fetch_rss(url, name):
    print(f"Fetching RSS: {name} ...")
    feed = fetch_rss_raw(url)
    if feed.bozo:
        print(f"RSS取得失敗（{name}）:", feed.bozo_exception)
        return []
    return feed.entries


# --------------------------------------------------
# 英語タイトル → 自然な日本語ニュースタイトルへ変換
# --------------------------------------------------
def translate_title_to_japanese(eng_title):
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    prompt = f"""
以下の英語ニュースタイトルを、日本語のニュース記事として自然で読みやすいタイトルに変換してください。
・直訳は避け、意味が伝わる自然な日本語にしてください。
・句読点と語順を「日本のニュース記事タイトル」風に整えてください。
英語タイトル:
{eng_title}
日本語タイトル：
"""

    res = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt
    )

    title = res.text.strip()
    title = title.replace("日本語タイトル：", "").strip()
    title = title.split("\n")[0].strip()
    return title


# --------------------------------------------------
# Gemini で Markdown 記事生成（レイアウト安定版）
# --------------------------------------------------
def generate_markdown(article):
    print("Generating article with Gemini...")

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    prompt = f"""
# 以下のフォーマットで日本語ニュース記事を生成してください。
# 絶対に余計な文章（注意書き・候補案など）を書かないこと。

元記事URL: {article["link"]}

## 概要
（要約）

## 背景
（背景説明。無い場合は空欄でOK）

## 今後の見通し
（予測が難しい場合はこのセクションを出さず、代わりに「## 推測」を使う）

## 推測
（このニュースが今後もたらす可能性のある影響）

--- 元記事情報 ---
英語タイトル:
{article["title"]}

概要（RSSの summary）:
{article["summary"]}

本文（抜粋）:
{article["content"]}

"""

    res = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt
    )

    md = res.text

    # 余計な先頭の # タイトル削除
    lines = md.split("\n")
    if lines[0].startswith("#"):
        lines = lines[1:]
    md = "\n".join(lines).lstrip()

    # 改行の正規化
    md = re.sub(r"\n{3,}", "\n\n", md)
    md = re.sub(r"\n+\s*##", "\n\n##", md)

    return md


# --------------------------------------------------
# Hatenaブログへ投稿
# --------------------------------------------------
def post_to_hatena(title, content_md):
    hatena_id = os.environ["HATENA_ID"]
    api_key = os.environ["HATENA_API_KEY"]
    blog_id = os.environ["HATENA_BLOG_ID"]

    content_html = markdown.markdown(content_md, extensions=["extra"])

    url = f"https://blog.hatena.ne.jp/{hatena_id}/{blog_id}/atom/entry"

    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<entry xmlns="http://www.w3.org/2005/Atom"
       xmlns:app="http://www.w3.org/2007/app">

  <title>{title}</title>

  <content type="text/html">
{content_html}
  </content>

</entry>
"""

    print("Posting to Hatena Blog...")
    r = requests.post(url, data=xml.encode("utf-8"), auth=(hatena_id, api_key))

    if r.status_code not in [200, 201]:
        print("Hatena投稿失敗:", r.status_code, r.text)
        return False

    print("Hatena投稿成功")
    return True


# --------------------------------------------------
# メイン処理
# --------------------------------------------------
def main():
    print("==== Myanmar News Auto Poster ====")

    seen = load_seen()

    RSS_SOURCES = [
        ("Irrawaddy", "https://www.irrawaddy.com/feed"),
    ]

    new_articles = []

    # RSS 取得
    for name, url in RSS_SOURCES:
        entries = fetch_rss(url, name)
        print("取得件数:", len(entries))

        for e in entries:
            link = e.get("link")
            if not link or link in seen:
                continue

            summary = BeautifulSoup(e.get("summary", ""), "html.parser").get_text()
            content = e.get("content", [{"value": summary}])[0]["value"]
            content = BeautifulSoup(content, "html.parser").get_text()

            new_articles.append({
                "id": link,
                "title": e.get("title", ""),
                "summary": summary,
                "content": content,
                "link": link
            })

    print("新規記事:", len(new_articles))

    if not new_articles:
        print("新記事なし。終了します。")
        return

    # 記事ごとに投稿
    for article in new_articles:
        # 日本語タイトル生成
        print("Translating title...")
        jp_title = translate_title_to_japanese(article["title"])
        print("日本語タイトル:", jp_title)

        md = generate_markdown(article)

        ok = post_to_hatena(jp_title, md)

        if ok:
            seen.add(article["id"])
            save_seen(seen)


# --------------------------------------------------
if __name__ == "__main__":
    main()
