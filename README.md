# AIコメント太郎 v4.00

Twitch配信中に配信者の音声をリアルタイムで認識し、文脈に沿った自然な日本語コメントを自動投稿するAI botです。

## 特徴

- 🎤 **音声認識**: faster-whisper（ローカルGPU/CPU・課金なし・高精度）
  - NVIDIA GPUがあれば自動でCUDA実行、なければCPUで動作
  - 初期化に失敗した場合はGoogle Web Speech APIに自動フォールバック
- 🤖 **コメント生成**: Gemini API（gemini-2.5-flash-lite・無料枠あり）
- 💬 **会話ステート管理**: 話題を振る→深掘り→着地の自然な会話の流れ
- 🗣️ **呼びかけ対応**: 「太郎、○○して」で即座に反応（クールダウン無視）
- 👥 **視聴者学習**: よく来る視聴者の呼び名を覚えて名前で反応
- 🎋 **俳句・謎かけイベント**: 配信の雰囲気を汲んだ一句を定期投稿
- 🛡️ **安全機構**: NGワードフィルター・プロンプト漏洩ガード・Whisper幻聴フィルター

## 動作環境

- Windows 11
- Python 3.10以上
- NVIDIA GPU推奨（RTX 2060以上）※CPUでも動作可
- インターネット接続（Twitch接続・Gemini API用）

## セットアップ

### 1. リポジトリをクローン

```bash
git clone https://github.com/higeremu-ttv/ai-comment-taro.git
cd ai-comment-taro/ai_taro
```

### 2. ライブラリのインストール

```bash
pip install -r requirements.txt
```

GPU（CUDA）で使う場合は追加で：

```bash
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
```

### 3. 設定ファイルの作成

`secrets_sample.py` をコピーして `taro_secrets.py` を作成します。

> ⚠️ ファイル名は必ず `taro_secrets.py` にしてください。
> 旧名 `secrets.py` はPython標準ライブラリと衝突し、Whisperが起動できません。

`taro_secrets.py` をテキストエディタで開き、以下を設定：

```python
BOT_NICK = "your_bot_account_name"   # botアカウント名
BOT_TOKEN = "xxxxxxxxxx"             # OAuthトークン（get_token.pyで取得）
CHANNEL_NAME = "your_channel_name"   # チャンネル名
GEMINI_API_KEY = "AIzaSy..."         # Gemini APIキー
```

### 4. 起動

`start_bot.bat` をダブルクリック、またはコマンドプロンプトで：

```bash
python gui_app.py
```

初回起動時はWhisperモデル（mediumで約1.5GB）のダウンロードが走るため数分かかります。2回目以降は即起動します。

## 音声認識の設定（config.py）

```python
SPEECH_ENGINE = "whisper"        # "google" にすると従来方式
WHISPER_MODEL_SIZE = "medium"    # tiny / base / small / medium / large-v3
WHISPER_DEVICE = "auto"          # "auto" / "cuda" / "cpu"
WHISPER_INITIAL_PROMPT = "..."   # よく出る固有名詞を書くと認識精度UP
```

| モデル | VRAM目安 | 特徴 |
|--------|---------|------|
| small | 約1GB | 軽量・そこそこの精度 |
| medium | 約2.5GB | バランス型（推奨） |
| large-v3 | 約5GB | 最高精度 |

## APIキーの取得

- **Gemini API**: [Google AI Studio](https://aistudio.google.com/app/apikey)（無料枠あり）
- **Twitch OAuth**: `python get_token.py` を実行

## ライセンス

MIT License
