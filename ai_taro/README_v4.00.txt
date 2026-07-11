■■■ AIコメント太郎 v4.00 - ローカルWhisper化 ■■■

音声認識をGoogle Web Speech APIからfaster-whisper（ローカル・課金なし）に
切り替えました。RTX 4080 SUPERのGPUで動くため、認識精度が大幅に向上し、
「太郎」が「牙狼」になる類の誤変換が激減します。

━━━━━━━━━━━━━━━━━━━━━━━━━━━
■ 適用手順（順番通りにやってください）
━━━━━━━━━━━━━━━━━━━━━━━━━━━

【1】ファイルを上書きコピー
   このフォルダの8ファイルを ai_taro フォルダに上書き
   （.gitignore も含む）

【2】secrets.py をリネーム ★重要★
   ai_taro\secrets.py → ai_taro\taro_secrets.py に名前変更

   理由: 「secrets」はPython標準ライブラリと同名のため、
   Whisperのライブラリが起動できない衝突が起きます。
   （リネームを忘れても起動はしますが、警告が出て
   　Whisperが使えずGoogle認識のままになります）

【3】ライブラリをインストール
   コマンドプロンプトで:
   cd C:\Users\remu\OneDrive\デスクトップ\twitch\twitch_bot\ai_taro
   pip install faster-whisper

【4】起動
   start_bot.bat で起動。
   ★初回のみWhisperモデル（約1.5GB）のダウンロードが走ります。
   　「Whisperモデルをロード中: medium / cuda / float16」のあと
   　数分待つとダウンロード完了して起動します。2回目以降は即起動。

【5】動作確認
   起動ログに以下が出ればGPUでWhisperが動いています:
   「認識エンジン: faster-whisper medium（CUDA・ローカル・課金なし）」

   マイクに「太郎、なんか話して」と話しかけて
   「[コメント太郎呼びかけ]」が出るか確認してください。

━━━━━━━━━━━━━━━━━━━━━━━━━━━
■ 設定（config.py）
━━━━━━━━━━━━━━━━━━━━━━━━━━━

SPEECH_ENGINE = "whisper"   ← "google"に戻すと従来方式
WHISPER_MODEL_SIZE = "medium"  ← 精度重視なら "large-v3"（VRAM約5GB）
WHISPER_DEVICE = "auto"     ← 通常はこのまま
WHISPER_INITIAL_PROMPT = "..." ← よく出る固有名詞をここに足すと認識精度UP

※Whisperの初期化に失敗した場合は自動でGoogle認識に切り替わるので、
　起動しなくなる心配はありません。

━━━━━━━━━━━━━━━━━━━━━━━━━━━
■ 技術メモ
━━━━━━━━━━━━━━━━━━━━━━━━━━━
・マイク取得・途中切れ結合・無言検知などの既存ロジックは全て温存。
　認識エンジン部分だけを差し替えています。
・無音時にWhisperが生成しがちな幻聴（「ご視聴ありがとうございました」等）
　は自動で破棄します。
・GPU使用量: mediumで約2.5GB（RTX 4080 SUPER 16GBの16%程度）。
　Fortniteと同居しても影響は軽微ですが、配信前に一度FPS確認を推奨。
