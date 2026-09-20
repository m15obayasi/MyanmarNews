# MyanmarNews

ミャンマー関連の記事を、はてなブログへ自動投稿する GitHub Actions プロジェクトです。

## 3つの投稿処理

- `main.py` / `run.yml`: 既存のニュース記事投稿。DVB、Myanmar Now、ReliefWeb の順に利用し、1つのRSSが一時停止しても次へ切り替えます。
- `explainer.py` / `explainer.yml`: 日本語の基礎解説を1日1本投稿します。未掲載テーマだけを候補にし、直近ニュースとの関連を参考にGeminiが選びます。
- `japan_life.py` / `japan-life.yml`: 日本在住のミャンマー人向け生活情報をミャンマー語で1日1本投稿します。入管庁・厚労省・内閣府などの公式資料だけを根拠にし、重要な日本語の用語を併記します。投稿先は `myanmar-japan-info.hatenablog.com` です。

解説記事は各媒体の公式記事API・RSSとGDELTのニュース索引から資料を探し、本文を取得できた異なる媒体が2つ以上ある場合だけ生成します。資料不足時は誤推測で埋めず、正常な掲載見送りとして終了します。掲載後は `explainer_topics.json` にテーマ、掲載日時、URL、使用資料を記録します。

## GitHub Secrets

必須:

- `GEMINI_API_KEY`
- `HATENA_ID`
- `HATENA_API_KEY`
- `HATENA_BLOG_ID`
- `HATENA_BLOG_ID_EN`（英語ニュース投稿を続ける場合）

生活情報ブログのIDは秘密情報ではないため、`japan-life.yml` に `myanmar-japan-info.hatenablog.com` として設定しています。

任意（4つすべて設定した場合だけXへ共有）:

- `X_API_KEY`
- `X_API_SECRET`
- `X_ACCESS_TOKEN`
- `X_ACCESS_TOKEN_SECRET`

値はコードやログに書かず、GitHub Secretsから環境変数として渡します。
Geminiのモデル名は秘密情報ではないため、ワークフロー内で `gemini-2.5-flash` を指定しています。

## 手動確認

Actions の `Daily Myanmar Explainer` から実行すると、既定では `dry_run=true` です。この場合はテーマ選定・複数資料の取得・記事生成まで確認しますが、ブログ投稿も履歴更新も行いません。

定期実行は毎日 10:15 JST を目安に動きます。ニュース投稿は従来どおり毎日 09:00 JST を目安に動きます。

生活情報は毎日 11:00 JST を目安に動きます。Actions の `Daily Japan Life Guide in Myanmar` から手動実行した場合は、既定で `dry_run=true` のため公開されません。`topic` には `residence-card-loss` など、`japan_life.py` に定義した未掲載テーマIDを任意指定できます。
