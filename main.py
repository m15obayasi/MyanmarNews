# main.py
# -*- coding: utf-8 -*-

import os
import base64
import datetime
import html
import textwrap

import requests
import markdown
from google import genai


# ===== 環境変数 =====
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
HATENA_ID = os.environ["HATENA_ID"]
HATENA_API_KEY = os.environ["HATENA_API_KEY"]
HATENA_BLOG_ID = os.environ["HATENA_BLOG_ID"]

# ===== Gemini クライアント設定 =====
# ★ポイント：api_version を v1 に固定
client = genai.Client(
    api_key=GEMINI_API_KEY,
    http_options=genai.HttpOptions(api_version="v1"),
)

# Gemini モデル名
MODEL_NAME = "gemini-1.5-flash"

# 日本時間
JST = datetime.timezone(datetime.timedelta(hours=9))


def escape_xml(text: str) -> str:
    """XML 用にエスケープ"""
    if text is None:
        return ""
    return html.escape(text, quote=True)


def build_title() -> str:
    """記事タイトルを自動生成（例：ミャンマー情勢まとめ（2025-11-22））"""
    now = datetime.datetime.now(JST)
    return f"ミャンマー情勢まとめ（{now.strftime('%Y-%m-%d')}）"


def generate_article_with_gemini() -> str:
    """Gemini でミャンマー解説記事（Markdown）を生成し、
    冒頭に「参照元」セクションを付けた全文 Markdown を返す。
    """
    system_prompt = (
        "あなたはミャンマー情勢に詳しい日本語のニュース解説者です。"
        "読者は日本在住の一般の人たちで、ミャンマーの状況には関心があるものの、専門家ではありません。"
    )

    user_prompt = """
以下の条件で、ミャンマーに関するニュース解説記事を書いてください。

- テーマ: ミャンマーの最近の政治・社会・人道状況をわかりやすく整理した解説
- 文字数: 日本語でおよそ 2000〜2500 文字
- 構成:
  - 導入
  - 背景（クーデター以降の流れをざっくり）
  - 最近の主な動き（複数のトピックを箇条書き＋解説）
  - 市民生活・人道状況への影響
  - 国際社会の対応
  - 今後の見通し
  - まとめ
- 出力形式: Markdown
- 見出しには「## 見出しタイトル」の形式を使う
- 箇条書きには「- 」を使う
- です・ます調で書く
- 陰謀論や未確認情報は避け、公的機関や信頼できるメディアが報じている
  「一般的に知られている事実」や傾向にとどめてください。
- 特定の日付や「本日」などの表現は避け、
  「最近」「ここ数年」「2020年代以降」などの表現を使ってください。
"""

    print("Generating article with Gemini...")

    # google-genai v1 の正しい呼び出し方
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=[
            {"role": "system", "parts": [{"text": system_prompt}]},
            {"role": "user", "parts": [{"text": user_prompt}]},
        ],
    )

    # レスポンスのテキストをまとめて取得
    body_md = response.text.strip()

    # 冒頭の「参照元」セクションをコード側で付与
    ref_section = textwrap.dedent(
        """\
        ## 参照元

        - この記事は外部ニュース記事を直接は参照せず、AI（Gemini）による自動生成の解説記事です。
        - 内容は一般に報じられているミャンマー情勢の傾向をもとにしていますが、
          最新の情報は各種ニュースサイトでご確認ください。

        ---
        """
    )

    full_md = ref_section + "\n\n" + body_md
    return full_md


def post_to_hatena(title: str, content_md: str) -> None:
    """はてなブログに投稿する（AtomPub + Basic認証）"""
    print("Posting to Hatena Blog...")

    # Markdown -> HTML
    html_body = markdown.markdown(content_md, extensions=["extra"])

    updated = datetime.datetime.now(datetime.timezone.utc).isoformat()

    entry_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <title>{escape_xml(title)}</title>
  <updated>{updated}</updated>
  <content type="text/html">{escape_xml(html_body)}</content>
  <category term="ミャンマー" />
  <category term="ニュース" />
  <author>
    <name>{escape_xml(HATENA_ID)}</name>
  </author>
</entry>
"""

    url = f"https://blog.hatena.ne.jp/{HATENA_ID}/{HATENA_BLOG_ID}/atom/entry"

    # Basic 認証ヘッダ（hatenaId:apiKey を Base64）
    auth_str = base64.b64encode(
        f"{HATENA_ID}:{HATENA_API_KEY}".encode("utf-8")
    ).decode("ascii")

    headers = {
        "Content-Type": "application/xml",
        "Authorization": f"Basic {auth_str}",
    }

    resp = requests.post(
        url,
        data=entry_xml.encode("utf-8"),
        headers=headers,
        timeout=30,
    )

    if not resp.ok:
        print("Hatena API error status:", resp.status_code)
        print(resp.text)
        resp.raise_for_status()

    print("Hatena Blog posted successfully.")


def main() -> None:
    print("==== Myanmar News Auto Poster (A: RSS無し・生成+投稿のみ) ====")

    # 記事本文（Markdown）生成
    article_md = generate_article_with_gemini()
    # タイトルはコード側で日付付きで作る
    title = build_title()

    # はてなブログに投稿
    post_to_hatena(title, article_md)

    print("Done.")


if __name__ == "__main__":
    main()
