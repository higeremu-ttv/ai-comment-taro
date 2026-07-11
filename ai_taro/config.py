"""
設定ファイル v4.11
個人設定は taro_secrets.py に分離しました。
このファイルはGitHubで管理されます（毎回上書きOK）。
"""

import importlib.util
import os
import sys

# 個人設定ファイルを読み込む
# v4.0: ファイル名を taro_secrets.py に変更（Python標準の secrets モジュールと
# 名前衝突し、faster-whisper等のライブラリが起動できなくなるため）
# 旧名 secrets.py もフォールバックで読めるが、Whisperが動かないため要リネーム
_base_dir = os.path.dirname(os.path.abspath(__file__))
_secrets_path = os.path.join(_base_dir, "taro_secrets.py")
_legacy_path = os.path.join(_base_dir, "secrets.py")

if os.path.exists(_secrets_path):
    pass  # 新名を使用
elif os.path.exists(_legacy_path):
    _secrets_path = _legacy_path
    import warnings
    warnings.warn(
        "【要対応】secrets.py を taro_secrets.py にリネームしてください。"
        "旧名のままだとPython標準ライブラリと衝突し、Whisper音声認識が起動できません。"
    )
else:
    raise RuntimeError(
        "taro_secrets.py が見つかりません。\n"
        "secrets_sample.py をコピーして taro_secrets.py を作成し、\n"
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
MICROPHONE_INDEX = 1
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
# ============================================
# 音声認識エンジン設定（v4.0）
# ============================================
# "whisper": faster-whisper（ローカルGPU/CPU・課金なし・高精度）※推奨
# "google" : Google Web Speech API（従来方式）
# whisperの初期化に失敗した場合は自動でgoogleにフォールバックします
SPEECH_ENGINE = "whisper"

# Whisperモデルサイズ: tiny / base / small / medium / large-v3
# RTX 4080 SUPERなら medium 推奨（VRAM約2.5GB・ゲームと同居可）
# さらに精度が欲しければ large-v3（VRAM約5GB）
WHISPER_MODEL_SIZE = "medium"

# 使用デバイス: "auto"（CUDA→CPUの順で自動選択） / "cuda" / "cpu"
WHISPER_DEVICE = "auto"

# 認識精度を上げるための語彙ヒント（配信でよく出る固有名詞を書いておく）
WHISPER_INITIAL_PROMPT = "Twitchのゲーム配信。太郎、コメント太郎、フォートナイト、ビクロイ、オリジンパス、抽出、ナイスファイト、ぶり大根、しらす姐さん、ターボさん、あおちゃん、まゆぽん、などの言葉が出ます。"

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
# v4.10: COMMENT_COOLDOWN_SECONDS は「文脈レーンの最低コメント間隔」の意味に
# なりました。呼びかけ・視聴者コマンドなどの即時レーンはこの間隔を無視します。
COMMENT_COOLDOWN_SECONDS = 45
COMMENT_RETRY_COUNT = 0
COMMENT_RETRY_INTERVAL = 5
SILENCE_COMMENT_THRESHOLD = 120
SPEECH_RESPONSE_DELAY = 3
MAX_SPEECH_CONTEXT_CHARS = 500

# ============================================================
# 二レーン化設定（v4.10 / Phase 2）
# ============================================================
# 会話の切れ目とみなす無音秒数。配信者が喋り終えてこの秒数静かになったら、
# 貯めた文脈からコメントを1つ生成する。静かすぎると感じたら下げる（5など）
CONTEXT_GAP_SECONDS = 7

# 普通の視聴者コメント（太郎宛てでないもの）に「ちょっかい」をかける確率（0.0〜1.0）
CHOKKAI_PROBABILITY = 0.25

# ちょっかい同士の最低間隔（秒）
CHOKKAI_MIN_INTERVAL = 120

# 送信キューの連投防止間隔（秒）。ペース管理はレーン側が持つため、ここは
# 「物理的に連続送信しない」ための最低限の値でよい
SEND_MIN_INTERVAL_SECONDS = 2

# --- v4.11: 薄い材料でのオウム返し防止 ---
# 文脈メモの合計がこの文字数未満なら、生成を見送って材料が貯まるのを待つ
CONTEXT_MIN_CHARS = 12
# ただし最初の発言からこの秒数が経ったら、薄くても生成する（反応しなさすぎ防止）
CONTEXT_FORCE_AFTER_SECONDS = 120
# 同じ材料で生成に失敗したら何回で見送るか（無限再挑戦によるAPI無駄撃ち防止）
CONTEXT_MAX_RETRY = 2

# ============================================================
# ゲーム画面認識設定
# ============================================================

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
COMMENT_MAX_TOKENS = 300

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

# 視聴者コメントに反応するか（v4.10: ちょっかい機能のON/OFFスイッチ。
# 太郎への名指しコメントはこの設定に関わらず常に返答する）
VIEWER_COMMENT_REACTION_ENABLED = True

# v4.10: 反応の頻度は CHOKKAI_PROBABILITY / CHOKKAI_MIN_INTERVAL（上記）で調整

# 反応するボットアカウント（お知らせ系）カンマ区切り
REACTION_BOT_ACCOUNTS = "nightbot,streamelements,frostytools"

