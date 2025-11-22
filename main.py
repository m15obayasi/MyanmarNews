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
        json.dump(sorted(list(seen)), f, ensure_ascii=False, indent=2)

# --------------------------------------------------
# RSS取得
# --------------------------------------------------
def fetch_rss(url, name):
    print(f"Fetching RSS: {name} ...")
    try:
        feed = feedparser.parse(url)
        if feed.bozo:
            print(f"RSS取得失敗（{name}）:", feed.bozo_exception)
            return []
        return feed.entries
    except Exception as e:
        print(f"RSS例外発生 ({name}): {e}")
        return []

# --------------------------------------------------
# 英語タイトル → 自然な日本語ニュースタイトルへ翻訳
# --------------------------------------------------
def translate_title(title_en, client):
    prompt = f"""
以下の英語ニュースタイトルを、自然な日本語ニュース記事のタイトルに翻訳してください。
- 意訳OK
- 語順の最適化OK
- 丁寧すぎる文体は禁止
- 30文字〜60文字程度でまとめる
- 見出しに不要な語句をつけない

英語タイトル：
{title_en}

出力形式：タイトルのみ
"""

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt
    )

    text = response.text.strip()
    text = text.replace("**", "").strip()

    # 改行が入っていたら最初の行のみ使用
    return text.split("\n")[0].strip()

# --------------------------------------------------
# Gemini：本文生成（Markdown）
# --------------------------------------------------
def generate_markdown(article, title_ja, client):
    prompt = f"""
# 以下の形式で日本語の記事本文のみ生成してください。
# タイトルは生成しない（すでに決定しているため不要）

【本文の構造】
元記事URL: {article["link"]}

## 概要
（重要ポイントを3〜6行）

## 背景
（必要な場合のみ。なければ空欄でもOK）

## 今後の見通し
（予測が困難な場合、この見出しは出力せず代わりに以下を出力）

## 推測
（このニュースが与える影響・国際的反応などを記述）

【禁止事項】
- タイトルの再出力
- 翻訳案、候補案の提示
- 余計な前置き、注意書きの出力
- 英語タイトル再掲

--- 元記事情報 ---
英語タイトル:
{article["title"]}

概要:
{article["summary"]}

本文（英語元記事の抜粋）:
{article["content"]}
"""

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt
    )

    text = response.text

    # 余計なタイトル行が出ていたら削除
    lines = text.split("\n")
    if lines[0].startswith("#"):
        lines = lines[1:]

    text = "\n".join(lines).lstrip()

    # 改行整形：空行は最大2つまで
    text = re.sub(r"\n{3,}", "\n\n", text)

    # 見出し前後の余分な空白削除
    text = re.sub(r"\n+\s*##", "\n\n##", text)

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
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

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
            content = e.get("content", [{"value": summary}])[0]["value"]

            new_articles.append({
                "id": link,
                "title": e.get("title", ""),
                "summary": summary,
                "content": BeautifulSoup(content, "html.parser").get_text(),
                "link": link
            })

    print("新規記事:", len(new_articles))
    if not new_articles:
        print("新記事なし。終了します。")
        return

    # 記事ごとに処理
    for article in new_articles:
        print("翻訳中タイトル:", article["title"])
        title_ja = translate_title(article["title"], client)

        print("生成タイトル:", title_ja)

        md = generate_markdown(article, title_ja, client)

        ok = post_to_hatena(title_ja, md)
        if ok:
            seen.add(article["id"])
            save_seen(seen)

# --------------------------------------------------
if __name__ == "__main__":
    main()
