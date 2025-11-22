import os
import json
import time
import requests
from bs4 import BeautifulSoup
from google import genai
import markdown
import re

SEEN_FILE = "seen_articles.json"


# ======================================================
# seen_articles.json の読み込み / 初期化
# ======================================================
def load_seen():
    if not os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)
        return set()

    with open(SEEN_FILE, "r", encoding="utf-8") as f:
        return set(json.load(f))


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(seen)), f, ensure_ascii=False, indent=2)


# ======================================================
# RSSを「feedparserを使わず」安全に解析（Irrawaddy対策）
# ======================================================
def fetch_rss_safe(url):
    print(f"Fetching RSS safely: {url}")

    try:
        r = requests.get(url, timeout=15)
    except Exception as e:
        print("RSS取得失敗:", e)
        return []

    if r.status_code != 200:
        print("RSS HTTPエラー:", r.status_code)
        return []

    soup = BeautifulSoup(r.content, "xml")  # ← XMLとして読む（Irrawaddy対策）

    items = soup.find_all("item")
    articles = []

    for it in items:
        link = it.link.text if it.link else None
        title = it.title.text if it.title else ""
        description = it.description.text if it.description else ""
        summary = BeautifulSoup(description, "html.parser").get_text()

        articles.append({
            "link": link,
            "title": title,
            "summary": summary,
            "content": summary
        })

    return articles


# ======================================================
# Geminiで日本語ニュースタイトルを生成
# ======================================================
def generate_japanese_title(client, english_title):
    prompt = f"""
以下の英語ニュースタイトルを、日本語メディアの自然なタイトルに変換してください。
- 意味を正しく反映
- 語順を自然に
- 不要なコロンは削除
- 30〜45文字以内で簡潔に
- 余計な説明文は出さない

英語タイトル:
{english_title}

出力は「日本語タイトルのみ」。
    """

    while True:
        try:
            res = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt
            )
            title = res.text.strip()
            title = title.replace("\n", "").strip()
            return title
        except Exception as e:
            print("タイトル生成429待機...", e)
            time.sleep(3)


# ======================================================
# Geminiで記事本文 Markdown を生成
# ======================================================
def generate_article_body(client, article):
    prompt = f"""
あなたは日本語ニュース編集者です。
以下の条件で Markdown 記事を作成してください。

# 制約
- 見出しは必ず「## 概要」「## 背景」「## 今後の見通し」「## 推測」
- 出力は Markdown のみ（タイトルは含めない）
- 余計な注意書き・補足・翻訳案は禁止
- 語尾は淡々とした報道調
- 箇条書きは使ってOK

# 出力フォーマット
元記事URL: {article["link"]}

## 概要
（要約）

## 背景
（背景説明）

## 今後の見通し
（不明なら短く）

## 推測
（このニュースが与える可能性）

--- 元記事情報 ---
英語タイトル:
{article["title"]}

概要:
{article["summary"]}

本文抜粋:
{article["content"]}
"""

    while True:
        try:
            res = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt
            )
            md = res.text.strip()

            # 見出し前の不要な行を整形
            md = re.sub(r"\n{3,}", "\n\n", md)
            md = re.sub(r"\n+\s*##", "\n\n##", md)

            return md
        except Exception as e:
            print("本文生成429待機...", e)
            time.sleep(3)


# ======================================================
# はてなブログ投稿（XML安全版）
# ======================================================
def post_to_hatena(title, md):
    hatena_id = os.environ["HATENA_ID"]
    api_key = os.environ["HATENA_API_KEY"]
    blog_id = os.environ["HATENA_BLOG_ID"]

    # Markdown → HTML（きれいに変換）
    content_html = markdown.markdown(md, extensions=["extra"])

    # XMLに禁止文字が入ると死ぬのでエスケープ
    content_html = content_html.replace("&", "&amp;") \
                               .replace("<", "&lt;") \
                               .replace(">", "&gt;")

    title_xml = title.replace("&", "&amp;") \
                     .replace("<", "&lt;") \
                     .replace(">", "&gt;")

    url = f"https://blog.hatena.ne.jp/{hatena_id}/{blog_id}/atom/entry"

    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <title>{title_xml}</title>
  <content type="text/html">
{content_html}
  </content>
</entry>
"""

    headers = {"Content-Type": "application/xml"}

    try:
        r = requests.post(url, data=xml.encode("utf-8"), auth=(hatena_id, api_key))
        if r.status_code not in [200, 201]:
            print("投稿失敗:", r.status_code, r.text)
            return False
    except Exception as e:
        print("投稿エラー:", e)
        return False

    print("投稿成功:", title)
    return True


# ======================================================
# メイン
# ======================================================
def main():
    print("==== Myanmar Auto Poster (Stable Ver.) ====")

    seen = load_seen()

    RSS_URL = "https://www.irrawaddy.com/feed"

    # RSS取得（Irrawaddy対応版）
    items = fetch_rss_safe(RSS_URL)

    new_articles = [a for a in items if a["link"] and a["link"] not in seen]

    print("新規記事:", len(new_articles))

    if not new_articles:
        print("新記事なし。終了")
        return

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    for art in new_articles:

        print("=== タイトル生成 ===")
        jp_title = generate_japanese_title(client, art["title"])
        print("→", jp_title)

        print("=== 本文生成 ===")
        md = generate_article_body(client, art)

        ok = post_to_hatena(jp_title, md)

        if ok:
            seen.add(art["link"])
            save_seen(seen)
        else:
            print("投稿失敗 → seen に追加せずスキップ")


# ======================================================
if __name__ == "__main__":
    main()
