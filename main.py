import os
import json
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from openai import OpenAI
from requests.auth import HTTPBasicAuth

BASE_URL = "https://www.irrawaddy.com"
STATE_FILE = Path("seen_articles.json")

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])


# =============================
# Utility
# =============================
def fetch(url):
    """Irrawaddy の robots.txt に crawl-delay=10 とあるため少し遅延する"""
    time.sleep(3)
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return r.text


def load_seen():
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()))
    return set()


def save_seen(urls):
    STATE_FILE.write_text(json.dumps(sorted(urls), ensure_ascii=False))


# =============================
# Extract article links
# =============================
def extract_links(max_count=20):
    html = fetch(BASE_URL)
    soup = BeautifulSoup(html, "html.parser")

    urls = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("http"):
            url = href
        elif href.startswith("/"):
            url = BASE_URL + href
        else:
            continue

        if url.endswith(".html") and ("/news/" in url or "/opinion/" in url):
            urls.append(url)

    uniq = []
    seen = set()
    for u in urls:
        if u not in seen:
            seen.add(u)
            uniq.append(u)

    return uniq[:max_count]


# =============================
# Fetch article
# =============================
def fetch_article(url):
    html = fetch(url)
    soup = BeautifulSoup(html, "html.parser")

    title_tag = soup.find("h1")
    title = title_tag.get_text(strip=True) if title_tag else url

    paragraphs = [
        p.get_text(strip=True)
        for p in soup.find_all("p")
        if len(p.get_text(strip=True)) > 40
    ]

    return title, "\n".join(paragraphs)


# =============================
# AI Summary
# =============================
def summarize(title, body):
    prompt = f"""
以下の英語記事を読んで、日本語で以下を出力してください。

1. 概要（3〜5文）
2. 今後の展望（2〜3文）

---
TITLE: {title}
ARTICLE:
{body}
---
""".strip()

    res = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "user", "content": prompt}
        ],
        temperature=0.3
    )
    return res.choices[0].message.content


# =============================
# Hatena Blog 投稿
# =============================
def post_to_hatena(title, html):
    hatena_id = os.environ["HATENA_ID"]
    api_key = os.environ["HATENA_API_KEY"]
    blog_id = os.environ["HATENA_BLOG_ID"]

    endpoint = f"https://blog.hatena.ne.jp/{hatena_id}/{blog_id}/atom/entry"

    xml = f"""
<entry xmlns="http://www.w3.org/2005/Atom">
  <title>{title}</title>
  <content type="text/html"><![CDATA[
  {html}
  ]]></content>
  <updated>{datetime.utcnow().isoformat()}Z</updated>
  <author><name>{hatena_id}</name></author>
  <category term="Myanmar News"/>
</entry>
""".strip()

    r = requests.post(
        endpoint,
        data=xml.encode("utf-8"),
        auth=HTTPBasicAuth(hatena_id, api_key),
        headers={"Content-Type": "application/xml"}
    )

    if r.status_code not in (200, 201):
        raise Exception(f"Hatena post failed: {r.status_code}\n{r.text}")


# =============================
# MAIN
# =============================
def main():
    today = datetime.now().strftime("%Y-%m-%d")
    seen = load_seen()
    links = extract_links()

    new_links = [u for u in links if u not in seen]

    if not new_links:
        print("No new articles.")
        return

    print(f"{len(new_links)} new articles found.")

    html = f"<h1>Irrawaddy ミャンマーニュースまとめ ({today})</h1><hr>"

    for url in new_links:
        title, body = fetch_article(url)
        summary = summarize(title, body)

        html += f"<h2>{title}</h2>"
        html += f'<p><a href="{url}" target="_blank">{url}</a></p>'
        html += f"<pre>{summary}</pre><hr>"

        seen.add(url)

    save_seen(seen)

    post_to_hatena(f"Irrawaddy ミャンマーニュースまとめ ({today})", html)


if __name__ == "__main__":
    main()
