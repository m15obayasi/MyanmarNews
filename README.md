# MyanmarNews

ミャンマー関連の記事を、はてなブログへ自動投稿する GitHub Actions プロジェクトです。

## 2つの投稿処理

- `main.py` / `run.yml`: 既存のニュース記事投稿。DVB、Myanmar Now、ReliefWeb の順に利用し、1つのRSSが一時停止しても次へ切り替えます。
- `explainer.py` / `explainer.yml`: 日本語の基礎解説を1日1本投稿します。未掲載テーマだけを候補にし、直近ニュースとの関連を参考にGeminiが選びます。

解説記事はGDELTのニュース索引から信頼対象ドメインの記事を探し、本文を取得できた異なる媒体が2つ以上ある場合だけ生成します。資料不足時は誤推測で埋めず、投稿せずに失敗させます。掲載後は `explainer_topics.json` にテーマ、掲載日時、URL、使用資料を記録します。

## GitHub Secrets

必須:

- `GEMINI_API_KEY`
- `GEMINI_MODEL`（未設定なら `gemini-2.5-flash`）
- `HATENA_ID`
- `HATENA_API_KEY`
- `HATENA_BLOG_ID`
- `HATENA_BLOG_ID_EN`（英語ニュース投稿を続ける場合）

任意（4つすべて設定した場合だけXへ共有）:

- `X_API_KEY`
- `X_API_SECRET`
- `X_ACCESS_TOKEN`
- `X_ACCESS_TOKEN_SECRET`

値はコードやログに書かず、GitHub Secretsから環境変数として渡します。

## 手動確認

Actions の `Daily Myanmar Explainer` から実行すると、既定では `dry_run=true` です。この場合はテーマ選定・複数資料の取得・記事生成まで確認しますが、ブログ投稿も履歴更新も行いません。

定期実行は毎日 10:15 JST を目安に動きます。ニュース投稿は従来どおり毎日 09:00 JST を目安に動きます。
