import os
import json
import time
import html
import requests
import feedparser
from bs4 import BeautifulSoup
from google import genai
import markdown
import re

SEEN_FILE = "seen_articles.json"


# --------------------------------------------------
# seen_articles.json の読み込み/保存
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
# RSS取得
# --------------------------------------------------
def fetch_rss(url, name):
    print(f"Fetching RSS: {name}")
    feed = feedparser.parse(url)
    if feed.bozo:
        print("RSS Error:", feed.bozo_exception)
        return []
    print(f"取得件数({name}):", len(feed.entries))
    return feed.entries


# --------------------------------------------------
# Gemini による日本語タイトル生成
# --------------------------------------------------
def generate_japanese_title(title_en):
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    prompt = f"""
以下の英語ニュースタイトルを、日本語の自然なニュース記事タイトルに変換してください。

・直訳ではなく、日本語ニュース記事の語順に整える
・主語が不要なら省略してよい
・短く、簡潔に、ニュース見出しらしく
・絶対に本文を書かない（タイトルのみ）

英語タイトル:
{title_en}
"""

    res = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt
    )

    text = res.text.strip()

    # もし見出し語が含まれてしまったら除去
    text = re.sub(r"^「|」$", "", text)
    text = text.replace("#", "").strip()

    return text


# --------------------------------------------------
# Gemini による本文生成（Markdown）
# --------------------------------------------------
def generate_markdown(article):
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    prompt = f"""
以下の形式で、日本語ニュース記事を Markdown で生成してください。

【重要ルール】
- 見出しは必ず「## 概要」「## 背景」「## 今後の見通し」または「## 推測」
- 「今後の見通し」が書けない場合は「## 推測」に切り替える
- タイトルを本文に書かない
- 元記事URLは本文の一番上に明記する
- 補足説明・警告・注意書きは絶対に書かない
- 出力は Markdown のみ

【出力フォーマット】

元記事URL: {article["link"]}

## 概要
（ニュース要約）

## 背景
（背景情報が無い場合は空欄でよい）

## 今後の見通し
（予測できない場合はこのブロックを出さず、代わりに↓）

## 推測
（このニュースがもたらす影響を推測）

--- 英語ニュース情報（参考のため記載） ---
タイトル:
{article["title"]}

概要:
{article["summary"]}

本文抜粋:
{article["content"]}
"""

    res = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt
    )

    md = res.text.strip()

    # 見出し整形
    md = re.sub(r"\n{3,}", "\n\n", md)
    md = re.sub(r"\n+\s*##", "\n\n##", md)

    return md


# --------------------------------------------------
# はてなブログ投稿（Markdown→HTML→XML escape）
# --------------------------------------------------
def post_hatena(title, md):
    hatena_id = os.environ["HATENA_ID"]
    api_key = os.environ["HATENA_API_KEY"]
    blog_id = os.environ["HATENA_BLOG_ID"]

    # Markdown → HTML
    html_body = markdown.markdown(md, extensions=["extra"])

    # XML escape（400エラー防止の最重要処理）
    esc_title = html.escape(title)
    esc_body = html.escape(html_body)

    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <title>{esc_title}</title>
  <content type="text/html">{esc_body}</content>
</entry>"""

    url = f"https://blog.hatena.ne.jp/{hatena_id}/{blog_id}/atom/entry"

    headers = {"Content-Type": "application/xml"}

    r = requests.post(url, data=xml.encode("utf-8"),
                      auth=(hatena_id, api_key),
                      headers=headers)

    if r.status_code in [200, 201]:
        print("=== 投稿成功 ===")
        return True

    print("投稿失敗:", r.status_code, r.text)
    return False


# --------------------------------------------------
# メイン処理
# --------------------------------------------------
def main():
    print("==== Myanmar News Auto Poster ====")

    seen = load_seen()

    RSS = [
        ("Irrawaddy", "https://www.irrawaddy.com/feed")
    ]

    new_articles = []

    for name, url in RSS:
        entries = fetch_rss(url, name)
        for e in entries:
            link = e.get("link")
            if not link or link in seen:
                continue

            summary = BeautifulSoup(e.get("summary", ""), "html.parser").get_text()
            raw_content = e.get("content", [{"value": summary}])[0]["value"]
            content = BeautifulSoup(raw_content, "html.parser").get_text()

            new_articles.append({
                "id": link,
                "title": e.get("title", ""),
                "summary": summary,
                "content": content,
                "link": link
            })

    print("新規記事:", len(new_articles))

    if not new_articles:
        print("新記事なし。終了")
        return

    for article in new_articles:
        print("\n=== タイトル生成 ===")
        ja_title = generate_japanese_title(article["title"])
        print("→", ja_title)

        print("=== 本文生成 ===")
        md = generate_markdown(article)

        ok = post_hatena(ja_title, md)

        if ok:
            seen.add(article["id"])
            save_seen(seen)
        else:
            print("投稿失敗…次の記事へ")


# --------------------------------------------------
if __name__ == "__main__":
    main()
