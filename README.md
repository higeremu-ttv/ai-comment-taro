# AIコメント太郎 v3.13

Twitch配信中に配信者の音声をリアルタイムで認識し、文脈に沿った自然な日本語コメントを自動投稿するAI botです。

## 特徴

- 🎤 **音声認識**: Google Web Speech API（無料）
- 🤖 **コメント生成**: Gemini API（gemini-2.5-flash-lite・無料枠あり）
- 🎮 **ゲーム画面認識**: Gemini APIマルチモーダル（VRAMを使用しない）
- 💬 **会話ステート管理**: 話題を振る→深掘り→着地の自然な会話の流れ
- 🎯 **ゲームプリセット**: games/フォルダにJSONを追加するだけでゲームごとに最適化

## 動作環境

- Windows 11
- Python 3.10以上
- インターネット接続必須

## セットアップ

### 1. リポジトリをクローン

```bash
git clone https://github.com/あなたのユーザー名/ai-comment-taro.git
cd ai-comment-taro/twitch_bot
```

### 2. ライブラリのインストール

```bash
pip install -r requirements.txt
```

### 3. 設定ファイルの作成

```bash
cp config_sample.py config.py
```

`config.py` をテキストエディタで開き、以下を設定してください：

```python
BOT_NICK = "your_bot_account_name"   # botアカウント名
BOT_TOKEN = "oauth:xxxxxxxxxx"       # OAuthトークン
CHANNEL_NAME = "your_channel_name"   # チャンネル名
GEMINI_API_KEY = "AIzaSy..."         # Gemini APIキー
```

### 4. 起動

`start_bot.bat` をダブルクリック、またはコマンドプロンプトで：

```bash
python gui_app.py
```

## ゲームプリセットの追加

`games/` フォルダにJSONファイルを追加するだけで対応ゲームが増えます。
詳しくは `games/README.md` を参照してください。

プルリクエスト歓迎です！

## 現在対応ゲーム

- フォートナイト (`games/fortnite.json`)

## APIキーの取得

- **Gemini API**: [Google AI Studio](https://aistudio.google.com/app/apikey)（無料枠: 1日1500リクエスト）
- **Twitch OAuth**: `python get_token.py` を実行

## ライセンス

MIT License
