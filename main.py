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
# RSS 取得
# --------------------------------------------------
def fetch_rss(url, name):
    print(f"Fetching RSS: {name} ...")
    feed = feedparser.parse(url)
    if feed.bozo:
        print(f"RSS取得失敗（{name}）:", feed.bozo_exception)
        return []
    return feed.entries


# --------------------------------------------------
# 英語タイトル → 日本語ニュース見出しに最適翻訳
# --------------------------------------------------
def translate_title_to_japanese(english_title):
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    prompt = f"""
以下の英語ニュースタイトルを、日本語ニュース見出し独特の語順に最適化して翻訳してください。

【厳守ルール】
- 出力は日本語タイトル「1行のみ」
- 説明文・翻訳案・理由付け・補足は禁止
- 主語 → 動詞 → 対象 の日本語報道語順に整形
- 文末に句点「。」は付けない
- カタカナ固有名詞は自然な形へ
- 冗長表現は適度に省く

英語タイトル:
{english_title}
"""

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt
    )

    title = response.text.strip()
    title = title.split("\n")[0].strip()
    return title


# --------------------------------------------------
# Gemini で Markdown 記事生成
# --------------------------------------------------
def generate_markdown(article):
    print("Generating article with Gemini AI...")

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    prompt = f"""
以下のニュース内容を基に、Markdown の記事本文を生成してください。

【絶対ルール】
- タイトルは本文に書かない（はてなブログの title と重複するため）
- 見出しは「概要」「背景」「今後の見通し」「推測」のいずれか
- 「翻訳案」「候補案」「補足」「注意書き」禁止
- 余計な文章を一切書かない
- 文中の英語タイトルは使わない（内容のみ参照）
- 見出しの順序は固定（概要 → 背景 → 今後の見通し or 推測）
- 見出しが空欄の場合：
    - 背景が書けない → 空欄のままでOK
    - 今後の見通しが書けない → 「推測」見出しを出す（内容を書く）

【出力フォーマット（Markdown）】
元記事URL: {article["link"]}

## 概要
（日本語で5〜10行）

## 背景
（必要なら記載。無い場合は空欄のまま）

## 今後の見通し
（予測可能な場合のみ記載）

## 推測
（見通しを書けない場合はこちらに、今後あり得る影響を記述）

--- 元記事情報 ---
英語タイトル:
{article["title"]}

概要:
{article["summary"]}

本文（英語の抜粋・サマリ）:
{article["content"]}
"""

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt
    )

    text = response.text.strip()

    # タイトル行が勝手に出たら除去
    if text.startswith("#"):
        text = "\n".join(text.split("\n")[1:]).lstrip()

    # 連続空行を整形
    text = re.sub(r"\n{3,}", "\n\n", text)

    # 見出し前は必ず 1 行空ける
    text = re.sub(r"\n+##", r"\n\n##", text)

    return text


# --------------------------------------------------
# はてなブログ投稿（Markdown → HTML）
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

    # RSS取得
    for name, url in RSS_SOURCES:
        entries = fetch_rss(url, name)
        print("取得件数:", len(entries))

        for e in entries:
            link = e.get("link")
            if not link or link in seen:
                continue

            summary = BeautifulSoup(e.get("summary", ""), "html.parser").get_text()
            content_raw = e.get("content", [{"value": summary}])[0]["value"]
            content = BeautifulSoup(content_raw, "html.parser").get_text()

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

    # 記事生成＆投稿
    for article in new_articles:
        jp_title = translate_title_to_japanese(article["title"])
        print("投稿タイトル:", jp_title)

        md = generate_markdown(article)

        ok = post_to_hatena(jp_title, md)

        if ok:
            seen.add(article["id"])
            save_seen(seen)


# --------------------------------------------------
if __name__ == "__main__":
    main()
