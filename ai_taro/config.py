"""
設定ファイル v3.26
個人設定は secrets.py に分離しました。
このファイルはGitHubで管理されます（毎回上書きOK）。
"""

import importlib.util
import os
import sys

# secrets.py を明示的にパスを指定して読み込む（Python標準のsecretsモジュールと名前衝突を回避）
_secrets_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "secrets.py")
if not os.path.exists(_secrets_path):
    raise RuntimeError(
        "secrets.py が見つかりません。\n"
        "secrets_sample.py をコピーして secrets.py を作成し、\n"
        "APIキー・トークン等を設定してください。"
    )
_spec = importlib.util.spec_from_file_location("_user_secrets", _secrets_path)
_s = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_s)

def _get(key, default=None):
    return getattr(_s, key, default)

_bot_token_raw = _get("BOT_TOKEN", "")
BOT_TOKEN = "oauth:" + _bot_token_raw.replace('oauth:', '') if _bot_token_raw else ""
BOT_NICK = _get("BOT_NICK", "")
CHANNEL_NAME = _get("CHANNEL_NAME", "")
GEMINI_API_KEY = _get("GEMINI_API_KEY", "")
AI_NAME = _get("AI_NAME", "AIコメント太郎")
VIEWER_COMMAND_PREFIX = _get("VIEWER_COMMAND_PREFIX", "!AIコメント太郎")
MICROPHONE_INDEX = _get("MICROPHONE_INDEX", None)
SCREEN_MONITOR_INDEX = _get("SCREEN_MONITOR_INDEX", 1)
EXCLUDED_ACCOUNTS = _get("EXCLUDED_ACCOUNTS", "moobot,fossabot")
STREAMER_NAME = _get("STREAMER_NAME", "")
STREAMER_TOKEN = _get("STREAMER_TOKEN", "")
TWITCH_CLIENT_ID = _get("TWITCH_CLIENT_ID", "")
TWITCH_CLIENT_SECRET = _get("TWITCH_CLIENT_SECRET", "")
REACTION_BOT_ACCOUNTS = _get("REACTION_BOT_ACCOUNTS", "nightbot,streamelements")

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
SCREEN_RECOGNITION_ENABLED = False  # v3.25: デフォルト無効化（画面認識の精度問題のため）
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

# ============================================================
# NGワードフィルター設定
# ============================================================

# 音声認識結果またはコメントにこれらのワードが含まれていた場合はコメントしない
# Twitchの規約違反ワードや意図しない誤変換対策
# カンマ区切りで追加可能
NG_WORDS = (
    # 性的表現
    "セックス,エッチ,ポルノ,アダルト,エロ,淫ら,性交,射精,勃起,オナニー,マスターベーション,"
    "ちんこ,チンコ,ペニス,まんこ,マンコ,ヴァギナ,バギナ,クリトリス,アナル,肛門,"
    "おっぱい,胸触,痴漢,強姦,レイプ,輪姦,売春,買春,援交,援助交際,ソープ,"
    "下半身,股間,陰部,性器,裸,全裸,半裸,脱いで,脱げ,"
    # 暴力・ヘイト表現
    "殺す,殺せ,死ね,死んで,ぶっ殺,消えろ,うせろ,"
    "テロ,爆弾,爆破,虐殺,暴行,傷害,"
    # 差別表現
    "障害者,チョン,朝鮮,ユダヤ,黒人差別,ニガー"
)

# ============================================================
# 視聴者コメント・ボット通知への反応設定
# ============================================================

# 視聴者コメントに反応するか
VIEWER_COMMENT_REACTION_ENABLED = True

# 視聴者コメントへの反応クールダウン（秒）
VIEWER_COMMENT_REACTION_COOLDOWN = 120

# 反応するボットアカウント（お知らせ系）カンマ区切り
REACTION_BOT_ACCOUNTS = "nightbot,streamelements"

# 画面認識の過疎時トリガー設定
SCREEN_SPARSE_SILENCE = 180   # 音声認識なし○秒以上
SCREEN_SPARSE_CHAT = 180      # チャットなし○秒以上
SCREEN_SPARSE_COMMENT = 120   # コメント太郎の発言から○秒以上
