# Slack Pin to Notion Export

Slackの各チャンネルにピン留めされたHPHero-taskタスクと、`#nicehero` / `#knowledge` の直近1週間のスレッドを収集し、Notionページまたは Markdown ファイルに出力するツール。

## 機能

- **ピン留めタスク収集** — 参加中の全チャンネルからHPHero-taskボットが投稿したピン留めメッセージを取得し、タスク名・ステータス・担当者・期日を抽出（「完了」「クローズ」は除外）
- **直近スレッド収集** — `#nicehero` / `#knowledge` の直近7日間のスレッドと返信を取得（転送メッセージの元スレッドも自動追跡）
- **Notion出力** — 収集結果をNotionページに構造化して出力
- **Markdownファイル出力** — ローカルにMarkdownファイルとして保存
- **フィルタ機能** — プライベートチャンネル除外、特定担当者除外に対応

## セットアップ

### 1. Slack App の準備

[Slack API](https://api.slack.com/apps) でAppを作成し、以下のUser Token Scopesを付与:

- `pins:read` — ピン留めメッセージの読み取り
- `channels:read` — パブリックチャンネル一覧の取得
- `channels:history` — チャンネルメッセージの読み取り
- `groups:read` — プライベートチャンネル一覧の取得
- `users:read` — ユーザー名の解決

### 2. 環境変数

`.env` ファイルまたは環境変数で以下を設定:

```
SLACK_USER_TOKEN=xoxp-...
NOTION_TOKEN=ntn_...        # Notion出力時のみ
NOTION_PAGE_ID=...           # Notion出力時のみ
```

### 3. 依存パッケージのインストール

```bash
pip install -r requirements.txt
```

## 使い方

```bash
# コンソールに出力
python get_pins.py

# Markdownファイルに出力
python get_pins.py --output pinned_messages.md

# Notionページに出力
python get_pins.py --notion

# プライベートチャンネルを除外してNotionに出力
python get_pins.py --notion --public-only

# 特定担当者を除外してNotionに出力
python get_pins.py --notion --exclude-assignees "担当者A" "担当者B"

# フィルタ付きでNotionに出力（プライベートチャンネル除外 + 特定担当者除外）
python get_pins.py --notion --public-only --exclude-assignees "担当者A" "担当者B"
```

## GitHub Actions による自動実行

[imtkzk/slack-pin](https://github.com/imtkzk/slack-pin) リポジトリの `.github/workflows/notion-export.yml` により、毎週木曜 16:00 (JST) に自動でNotionへエクスポートされます。手動実行（workflow_dispatch）も可能です。

必要なRepository Secrets:

- `SLACK_USER_TOKEN`
- `NOTION_TOKEN`
- `NOTION_PAGE_ID`
