import os
import json
from datetime import datetime
from pathlib import Path

import requests
import feedparser
from bs4 import BeautifulSoup
from google import genai

# ============================
# 設定
# ============================

RSS_URL = "https://myanmar-now.org/en/rss"
STATE_FILE = Path("seen_articles.json")

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])


# ============================
# seen 記録の読み書き
# ============================

def load_seen():
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()))
    return set()

def save_seen(urls):
    STATE_FILE.write_text(json.dumps(sorted(urls), ensure_ascii=False))


# ============================
# RSS取得
# ============================

def extract_links(max_count=10):
    print("Fetching Myanmar Now RSS...")

    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        r = requests.get(RSS_URL, timeout=20, headers=headers)
        r.raise_for_status()
    except Exception as e:
        print(f"RSS取得失敗: {e}")
        return []

    feed = feedparser.parse(r.text)

    if not feed.entries:
        print("RSSパース失敗。終了します。")
        return []

    print(f"RSSから {len(feed.entries[:max_count])} 件取得")

    urls = []
    for entry in feed.entries[:max_count]:
        urls.append(entry.link)

    return urls


# ============================
# 記事本文の取得
# ============================

def fetch_article_body(url):
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        r = requests.get(url, timeout=20, headers=headers)
        r.raise_for_status()
    except Exception:
        print("本文取得失敗")
        return ""

    soup = BeautifulSoup(r.text, "html.parser")

    # Myanmar Now の記事本文は <div class="field-item even"> 配下に多い
    article_div = soup.find("div", class_="field-item")

    if not article_div:
        return ""

    # テキスト抽出
    text = article_div.get_text(separator="\n", strip=True)

    return text


# ============================
# Gemini で記事生成
# ============================

def generate_article(title_en, body_en):
    prompt = f"""
あなたは日本語のニュース編集者です。
以下の英語ニュース本文をもとに、**日本語で記事を作成**してください。

【必須ルール】
- **記事タイトルは自然な日本語訳で「常体（〜した／〜を発表）」にする**
- **本文は丁寧な「です・ます調」にする**
- 文章量は **500〜900字**
- 構成は次の3つを必ず含める：
   1) 要約（です・ます調）
   2) 背景解説（です・ます調）
   3) 今後の見通し（です・ます調）

【英語のタイトル】
{title_en}

【英語本文】
{body_en}

丁寧で自然な日本語ニュース記事を生成してください。
"""

    response = client.models.generate_content(
        model="gemini-2.0-flash-thinking",
        contents=prompt,
    )

    return response.text.strip()


# ============================
# はてなブログ投稿
# ============================

def post_to_hatena(title, content):
    hatena_id = os.environ["HATENA_ID"]
    api_key = os.environ["HATENA_API_KEY"]
    blog_id = os.environ["HATENA_BLOG_ID"]

    url = f"https://blog.hatena.ne.jp/{hatena_id}/{blog_id}/atom/entry"

    headers = {
        "Content-Type": "application/xml",
    }

    # XML生成
    entry_xml = f"""
<entry xmlns="http://www.w3.org/2005/Atom">
  <title>{title}</title>
  <content type="text/plain">{content}</content>
</entry>
"""

    res = requests.post(url, data=entry_xml.encode("utf-8"), headers=headers,
                        auth=(hatena_id, api_key))

    if res.status_code not in (200, 201):
        print("Hatena 投稿失敗:", res.status_code)
        print(res.text)
        return False

    print("Hatena 投稿成功:", res.status_code)
    return True


# ============================
# メイン処理
# ============================

def main():
    seen = load_seen()
    urls = extract_links()

    new_urls = [u for u in urls if u not in seen]
    print(f"New articles: {len(new_urls)}")

    if not new_urls:
        print("新着なし。終了します。")
        return

    for url in new_urls:
        print(f"Fetching article: {url}")
        body_en = fetch_article_body(url)

        if not body_en:
            print("本文なし。スキップ。")
            seen.add(url)
            continue

        # 英語タイトルを取得する（簡易）
        title_en = url.split("/")[-2].replace("-", " ").title()

        article = generate_article(title_en, body_en)

        success = post_to_hatena(title_en, article)
        if success:
            seen.add(url)

    save_seen(seen)


if __name__ == "__main__":
    main()
