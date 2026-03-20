"""
設定ファイル v3.15
個人設定は secrets.py に分離しました。
このファイルはGitHubで管理されます（毎回上書きOK）。
"""

# secrets.py から個人設定を読み込む
try:
    from secrets import (
        BOT_NICK, BOT_TOKEN, CHANNEL_NAME, GEMINI_API_KEY,
        AI_NAME, VIEWER_COMMAND_PREFIX,
        MICROPHONE_INDEX, SCREEN_MONITOR_INDEX, GAME_TITLE,
        EXCLUDED_ACCOUNTS
    )
except ImportError:
    raise RuntimeError(
        "secrets.py が見つかりません。\n"
        "secrets_sample.py をコピーして secrets.py を作成し、\n"
        "APIキー・トークン等を設定してください。"
    )

# ============================================================
# Gemini API 設定
# ============================================================
GEMINI_MODEL = "gemini-2.5-flash-lite"

# ============================================================
# 音声認識設定
# ============================================================
MICROPHONE_ENERGY_THRESHOLD = 300
SPEECH_PAUSE_THRESHOLD = 2.0

# ============================================================
# 発言フィルター設定
# ============================================================
SPEECH_MIN_LENGTH = 4
UNRECOGNIZED_THRESHOLD = 6
INCOMPLETE_SPEECH_MERGE_ENABLED = True
INCOMPLETE_SPEECH_WAIT_SECONDS = 8
VC_COMMAND_WORDS = "右,左,上,下,行くぞ,行け,止まれ,待って,待て,来い,来て,頼む,お願い,了解,りょ,りょーかい,おけ,おっけー,OK,ok,yes,no,はい,いいえ,うん,いや,ちょっと待って,ちょい待ち,ちょっと,集合,解散,突撃,撤退,カバー,カバーして,蘇生,蘇生して,回復,回復して,ゴー,ゴーゴー,ストップ,バック,前,後ろ"

# ============================================================
# 会話ステート管理設定
# ============================================================
CONVERSATION_MAX_TURNS = 3
TOPIC_COOLDOWN_SECONDS = 60
TOPIC_END_KEYWORDS = "そうね,うん,なるほど,そっか,そうか,確かに,ですね,そうですね,わかった,了解,りょ,おけ,まあね,そうだね,そうだよね,そうかも,そうかもね"

# ============================================================
# タイミング制御設定
# ============================================================
COMMENT_COOLDOWN_SECONDS = 45
COMMENT_RETRY_COUNT = 0
COMMENT_RETRY_INTERVAL = 5
SILENCE_COMMENT_THRESHOLD = 120
SPEECH_RESPONSE_DELAY = 3
MAX_SPEECH_CONTEXT_CHARS = 500

# ============================================================
# ゲーム画面認識設定
# ============================================================
SCREEN_RECOGNITION_ENABLED = True
SCREEN_CAPTURE_INTERVAL = 300

# ============================================================
# 他の視聴者コメント監視設定
# ============================================================
CHAT_ACTIVITY_MUTE_ENABLED = True
CHAT_ACTIVITY_THRESHOLD = 3
CHAT_ACTIVITY_WINDOW_SECONDS = 60
CHAT_QUIET_RESUME_SECONDS = 30

# ============================================================
# AI キャラクター設定
# ============================================================
AI_DIRECT_MAX_TOKENS = 300
COMMENT_MAX_TOKENS = 120

# ============================================================
# 視聴者コマンド設定
# ============================================================
VIEWER_COMMANDS_ENABLED = True
COMMAND_HELLO_ENABLED = True
COMMAND_STATUS_ENABLED = True

# ============================================================
# ログ設定
# ============================================================
LOG_LEVEL = "INFO"
LOG_FILE = "twitch_bot.log"
