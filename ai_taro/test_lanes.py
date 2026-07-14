# -*- coding: utf-8 -*-
"""v4.10 lane_manager の動作テスト（API・マイク・Twitchなしで検証）"""
import sys
import time
import random
import os
import shutil

# ============================================================
# 手帳の隔離（ローカル移行に伴う保護。2026-07-15）
# CommentGenerator は自分のフォルダの learned_profile.json を開くため、
# そのまま実行すると実物の手帳にテストデータが書き込まれてしまう。
# ProfileManager の保存先が実物のフォルダだったときだけ、テスト用フォルダへ差し替える。
# あわせてテスト用フォルダを毎回まっさらにし、前回の実行結果に左右されないようにする。
# ============================================================
import profile_manager as _pm_module

_REAL_DIR = os.path.dirname(os.path.abspath(_pm_module.__file__))
_TEST_PROFILE_DIR = '/tmp/taro_test_profile'
for _d in (_TEST_PROFILE_DIR, '/tmp/pm2', '/tmp/pm_test'):
    shutil.rmtree(_d, ignore_errors=True)
os.makedirs(_TEST_PROFILE_DIR, exist_ok=True)

_pm_orig_init = _pm_module.ProfileManager.__init__

def _sandboxed_init(self, base_dir, *args, **kwargs):
    if os.path.abspath(base_dir) == _REAL_DIR:
        base_dir = _TEST_PROFILE_DIR
    _pm_orig_init(self, base_dir, *args, **kwargs)

_pm_module.ProfileManager.__init__ = _sandboxed_init

from comment_generator import CommentGenerator
from lane_manager import LaneManager


class FakeConfig:
    AI_NAME = "AIコメント太郎"
    STREAMER_NAME = "ひげさん"
    COMMENT_COOLDOWN_SECONDS = 45
    CONTEXT_GAP_SECONDS = 7
    CHOKKAI_PROBABILITY = 0.25
    CHOKKAI_MIN_INTERVAL = 120
    MAX_SPEECH_CONTEXT_CHARS = 500
    CHAT_ACTIVITY_MUTE_ENABLED = True
    CHAT_QUIET_RESUME_SECONDS = 30
    VIEWER_COMMENT_REACTION_ENABLED = True
    CONVERSATION_MAX_TURNS = 3
    TOPIC_COOLDOWN_SECONDS = 60
    NG_WORDS = "死ね,殺す"
    GEMINI_API_KEY = "dummy"
    INTERVIEW_ENABLED = False  # v4.50: 既存テストに取材が割り込まないよう既定OFF


class FakeTwitch:
    def __init__(self):
        self.sent = []          # (message, priority)
        self.chat_active = False

    def send_comment(self, m):
        self.sent.append((m, False))

    def send_comment_priority(self, m):
        self.sent.append((m, True))

    def is_chat_active(self):
        return self.chat_active

    def get_last_chat_time(self):
        return time.time() if self.chat_active else 0


class FakeAudio:
    def __init__(self):
        self.silence = 100.0
        self.corrections = {}

    def get_seconds_since_last_speech(self):
        return self.silence

    def set_corrections(self, corrections):
        self.corrections = corrections


results = []


def check(name, cond):
    results.append((name, cond))
    print(("OK   " if cond else "NG!! ") + name)


cfg = FakeConfig()
gen = CommentGenerator(cfg)
_counter = [0]

# v4.11の類似検出（2グラム重なり率0.45）に弾かれないよう、
# ニセ返答は内容が大きく異なる文を順番に返す
_FAKE_LINES = [
    "今日の立ち回りキレッキレだったね、正直しびれたよ！",
    "そのアイテムの使い方、初めて見たかも。勉強になるなあ。",
    "ここのマップって隠しチェスト多いから探検しがいあるよね。",
    "さっきの逃げ方は完全にプロの動きだったと思うんだけど！",
    "夜遅くまでお疲れさま、無理せずいこうね。",
    "次のマッチはビクロイの予感がビンビンするんだけど！",
    "武器ガチャ運が今日は良さそうだから期待しちゃうよ。",
    "建築バトルになると急に本気出すのさすがだよね。",
    "回復アイテムの管理が上手すぎて参考になるわ。",
    "敵の位置読みが冴えてて見てて気持ちいいんだよね。",
    "今の判断は英断だったんじゃない？俺は好きだよ。",
    "この時間帯は強い敵多いから気をつけていこうね。",
    "リスナーみんなで応援してるから思い切っていこう！",
    "そのスキン似合ってるね、色使いがおしゃれだと思う。",
    "次の安置どっちに寄ると思う？俺は北だと予想するよ。",
]


def _fake_gemini(prompt, **kw):
    """API呼び出しの差し替え。毎回まったく違う文を返す"""
    _counter[0] += 1
    return _FAKE_LINES[_counter[0] % len(_FAKE_LINES)]


gen._call_gemini = _fake_gemini
twitch = FakeTwitch()
audio = FakeAudio()
lanes = LaneManager(cfg, gen, twitch, audio)

# ---- 1. 呼びかけ判定（即時レーン・優先送信） ----
for call in ["太郎、今日の調子どう？", "コメント太郎！おすすめ教えて", "AIコメント太郎 なんか話して"]:
    twitch.sent.clear()
    lanes.on_speech(call)
    check(f"呼びかけ「{call[:12]}...」→優先送信", len(twitch.sent) == 1 and twitch.sent[0][1] is True)

# 「太郎」を含むが文頭でない → 呼びかけ扱いしない
twitch.sent.clear()
lanes._context_memo.clear()
lanes._conversation_until = 0.0  # v4.30: 直前の呼びかけで開いた会話モードの窓を閉じる
lanes.on_speech("さっき太郎がいいこと言ってたな")
check("文中の「太郎」→呼びかけ扱いしない（文脈メモへ）",
      len(twitch.sent) == 0 and len(lanes._context_memo) == 1)

# ---- 2. 独り言はメモに貯まる・即送信されない ----
lanes._context_memo.clear()
twitch.sent.clear()
for t in ["敵がいっぱいいるぞ", "やばいやばい囲まれた", "よし、ビクロイ狙うぞ"]:
    lanes.on_speech(t)
check("独り言3件→メモ3件・送信0件", len(lanes._context_memo) == 3 and len(twitch.sent) == 0)

# ---- 3. tick: 切れ目検知の条件 ----
# 条件不足1: 切れ目が来ていない（まだ喋ってる）
audio.silence = 2.0
lanes._last_comment_time = 0.0
lanes.tick()
check("喋ってる最中はコメントしない", len(twitch.sent) == 0)

# 条件不足2: 切れ目はあるがクールダウン中
audio.silence = 100.0
lanes._last_comment_time = time.time() - 10  # 10秒前にコメント済み
lanes.tick()
check("クールダウン中はコメントしない（メモは保持）",
      len(twitch.sent) == 0 and len(lanes._context_memo) == 3)

# 全条件クリア → まとめて1コメント・メモ空
lanes._last_comment_time = time.time() - 60
lanes.tick()
check("切れ目+間隔OK→まとめて1コメント送信", len(twitch.sent) == 1 and twitch.sent[0][1] is False)
check("送信後メモが空になる", len(lanes._context_memo) == 0)
check("メモ空のときtickは何もしない", (lanes.tick() or len(twitch.sent) == 1))

# ---- 4. チャット活発時は文脈レーン沈黙 ----
lanes.on_speech("これ見て見て")
twitch.sent.clear()
twitch.chat_active = True
lanes._last_comment_time = 0.0
lanes.tick()
check("チャット活発時は文脈コメントしない", len(twitch.sent) == 0)
twitch.chat_active = False
lanes._context_memo.clear()

# ---- 5. 生成失敗時はメモ復元＋リトライ待ち ----
lanes.on_speech("生成に失敗するテスト発言")
twitch.sent.clear()
orig = gen.generate_context_comment
gen.generate_context_comment = lambda d, **kw: None
lanes._last_comment_time = 0.0
lanes._next_context_attempt = 0.0
lanes.tick()
check("生成失敗→メモに復元される", len(lanes._context_memo) == 1)
check("生成失敗→30秒のリトライ待ちが入る", lanes._next_context_attempt > time.time() + 25)
gen.generate_context_comment = orig
lanes._context_memo.clear()
lanes._next_context_attempt = 0.0

# ---- 6. メモの容量上限（500文字） ----
for i in range(30):
    lanes._memo_append("あいうえおかきくけこ" * 5)  # 50文字×30回=1500文字
total = sum(len(e['text']) for e in lanes._context_memo)
check(f"メモ上限500文字が効く（現在{total}文字）", total <= 500)
lanes._context_memo.clear()

# ---- 7. 視聴者コメント：名指しは即時・優先 ----
twitch.sent.clear()
lanes.on_viewer_comment("太郎おもしろいなｗ", "turbo35gtr")
check("名指しコメント→優先送信", len(twitch.sent) == 1 and twitch.sent[0][1] is True)

# ---- 8. ちょっかい：確率と間隔 ----
twitch.sent.clear()
lanes._last_chokkai_time = 0.0
lanes._last_comment_time = 0.0
random.seed(1)  # random.random()の1回目=0.134 < 0.25 → 当たり
lanes.on_viewer_comment("今日も配信きたよ", "satoo_1976")
check("ちょっかい当たり→通常送信", len(twitch.sent) == 1 and twitch.sent[0][1] is False)

twitch.sent.clear()
lanes.on_viewer_comment("連続コメント", "satoo_1976")
check("ちょっかい直後120秒は再発動しない", len(twitch.sent) == 0)

lanes._last_chokkai_time = 0.0
lanes._last_comment_time = time.time()  # 太郎が直前にコメントした
twitch.sent.clear()
lanes.on_viewer_comment("また書くよ", "satoo_1976")
check("太郎コメント直後15秒はちょっかいしない", len(twitch.sent) == 0)

# ハズレ側: random>=0.25になるまで（seed=5の1回目=0.62）
lanes._last_chokkai_time = 0.0
lanes._last_comment_time = 0.0
random.seed(5)
twitch.sent.clear()
lanes.on_viewer_comment("外れるはずのコメント", "digitamama")
check("ちょっかい外れ→送信しない（履歴には残る）", len(twitch.sent) == 0)

# ---- 9. 視聴者コマンド → 即時・優先 ----
twitch.sent.clear()
lanes.on_viewer_command("ask:おすすめの武器は？", "yppiyo")
check("視聴者コマンド→優先送信", len(twitch.sent) == 1 and twitch.sent[0][1] is True)

# ---- 10. NGワード入り文脈はGeminiに送らない ----
gen2 = CommentGenerator(cfg)
called = []
gen2._call_gemini = lambda p: called.append(p) or "ダミー応答です、これは20文字以上あります"
r = gen2.generate_context_comment("・死ねとか言っちゃだめだよ")
check("NGワード入り文脈→生成スキップ", r is None and len(called) == 0)

# ---- 11. profile_manager のフォールバック修復確認 ----
from profile_manager import ProfileManager
pm = ProfileManager("/tmp/pm_test")
pm._update_from_conversation_regex([{"content": "こんにちは、ターボさんと遊んだよ"}])
check("プロフィール予備処理が動く（v4.00では即死）", "ターボさん" in pm._profile.get('known_friends', []))

# ---- 12. 会話ステートが文脈コメントでも進む ----
gen3 = CommentGenerator(cfg)
gen3._call_gemini = lambda p: f"ステート確認用のコメントです、いいね！({random.random()})"
s0 = gen3._conversation_state.value
c1 = gen3.generate_context_comment("・テスト発言その1だよ")
s1 = gen3._conversation_state.value
check(f"文脈コメントでステート遷移 {s0}→{s1}", c1 is not None and s1 == "topic_raised")

# ============================================================
# v4.11 の新機能テスト
# ============================================================

# ---- 13. 類似コメント検出 ----
gen4 = CommentGenerator(cfg)
gen4._record_comment("車、探してるんだね！どんな車が見つかるか、ワクワクするよ。")
check("空白違いの繰り返しを検出（v4.10ではすり抜け）",
      gen4._is_duplicate("車、探してるんだね！ どんな車が見つかるか、 ワクワクするよ。"))
check("言い換えの繰り返しを検出",
      gen4._is_duplicate("車、探してるんだね！どんな車が見つかるのか、ワクワクしちゃうよ。"))
check("別内容のコメントは誤検出しない",
      not gen4._is_duplicate("ラスト1対3だって？そこからの逆転劇、見たいじゃん！"))

# ---- 14. 薄い材料の見送り ----
lanes._context_memo.clear()
lanes._next_context_attempt = 0.0
lanes._context_fail_count = 0
twitch.sent.clear()
twitch.chat_active = False
audio.silence = 100.0
lanes._last_comment_time = 0.0
lanes.on_speech("おはよう。")  # 5文字 < CONTEXT_MIN_CHARS(12)
lanes.tick()
check("薄い材料(5文字)はすぐ生成しない（メモは保持）",
      len(twitch.sent) == 0 and len(lanes._context_memo) == 1)
lanes._context_memo[0]['time'] = time.time() - 130  # 120秒経過を偽装
lanes.tick()
check("時間が経ったら薄い材料でも生成する（反応しなさすぎ防止）", len(twitch.sent) == 1)

# ---- 15. 再挑戦の上限（無限ループ防止） ----
lanes._context_memo.clear()
lanes._next_context_attempt = 0.0
lanes._context_fail_count = 0
twitch.sent.clear()
lanes._last_comment_time = 0.0
orig2 = gen.generate_context_comment
gen.generate_context_comment = lambda d, **kw: None
lanes.on_speech("これは生成に失敗し続ける長さ十分なテスト発言です")
lanes.tick()
check("1回目の失敗→メモ復元して再挑戦待ち", len(lanes._context_memo) == 1)
lanes._next_context_attempt = 0.0
lanes.tick()
check("2回目の失敗→潔く見送り（実戦の6連敗を防ぐ）", len(lanes._context_memo) == 0)
gen.generate_context_comment = orig2

# ---- 16. ボット通知は同じ内容に1回だけ ----
twitch.sent.clear()
lanes._last_bot_reaction_time = 0.0
lanes._last_comment_time = 0.0
ad1 = "チャンネルポイントの「配信者カードガチャ」でカードをゲットしよう。"
lanes.on_viewer_comment(ad1, "nightbot", is_bot=True)
check("初めて見るボット通知→反応する", len(twitch.sent) == 1)
lanes._last_bot_reaction_time = 0.0  # クールダウンを解除しても…
lanes._last_comment_time = 0.0
lanes.on_viewer_comment(ad1, "nightbot", is_bot=True)
check("同じ通知の2回目→反応しない", len(twitch.sent) == 1)
lanes._last_bot_reaction_time = 0.0
lanes._last_comment_time = 0.0
lanes.on_viewer_comment("チンチロで遊べます。是非どうぞ。", "nightbot", is_bot=True)
check("別内容の通知→ちゃんと反応する", len(twitch.sent) == 2)

# ============================================================
# v4.20 手帳2.0 のテスト
# ============================================================
import json
import os
from profile_manager import ProfileManager as PM2

# ---- 17. 旧形式の手帳（v1）がv2へ安全に引き継がれる ----
# 元はCowork環境にあった実物のv1手帳をコピーしていたが、ローカル移行に伴い
# 同じ値を持つv1形式のテスト用データを直接書き込む方式に変更（検証内容は同一）
os.makedirs('/tmp/pm2', exist_ok=True)
_v1_profile = {
    "streamer_name": "ひげレム",
    "viewer_names": {"shirasu_gamech": "しらす姐さん", "turbo35gtr": "ターボさん"},
    "known_viewers": {"turbo35gtr": {"count": 81, "samples": []}},
    "recent_topics": [],
}
with open('/tmp/pm2/learned_profile.json', 'w', encoding='utf-8') as _f:
    json.dump(_v1_profile, _f, ensure_ascii=False)
pm2 = PM2('/tmp/pm2')
check("実物の手帳がv2形式に引き継がれる", pm2._profile.get('version') == 2)
check("既存データが消えない（ターボさん81回）",
      pm2._profile['known_viewers'].get('turbo35gtr', {}).get('count') == 81)
check("既存の呼び名対応が残る",
      pm2._profile['viewer_names'].get('shirasu_gamech') == 'しらす姐さん')
check("新しい欄（辞書・ネタ・近況）が生える",
      isinstance(pm2._profile.get('glossary'), dict)
      and isinstance(pm2._profile.get('jokes'), list)
      and isinstance(pm2._profile.get('streamer_status'), list))
pm2.save()
pm2b = PM2('/tmp/pm2')
check("保存→再読み込みしてもv2のまま", pm2b._profile.get('version') == 2)

# ---- 18. 用語辞書と「関連ページだけ貼る」参照 ----
pm2.add_glossary_term("オリジンパス", "フォートナイトのアイテム")
pm2.add_viewer_note("turbo35gtr", "PS配信派")
pages = pm2.get_relevant_pages("今日はオリジンパスが取れたよ", ["turbo35gtr"])
check("会話に出た用語のページが貼られる", "オリジンパス" in pages)
check("来ている視聴者のメモが貼られる（呼び名で）",
      "ターボさん" in pages and "PS配信派" in pages)
pages2 = pm2.add_glossary_term("ビクロイ", "勝利") or pm2.get_relevant_pages("全然関係ない天気の話", [])
check("関係ない話のときは用語ページを貼らない", "オリジンパス" not in (pages2 or ""))

# ---- 19. Whisperヒントへの自動連携 ----
terms = pm2.get_whisper_terms()
check("辞書の語がWhisperヒントに入る", "オリジンパス" in terms)
check("呼び名もWhisperヒントに入る", "ターボさん" in terms)
check("ヒントは20語以内", len(terms) <= 20)

from audio_module import AudioModule
am = AudioModule(cfg)
am.set_extra_vocabulary(terms)
check("音声モジュールに語彙が流れる", len(am._extra_vocab) > 0)

# ---- 20. 近況・定番ネタが基本ページに載る ----
pm2.add_streamer_status("新しいマイク検討中")
pm2.add_joke("黄色いひげ")
base = pm2.get_prompt_text()
check("近況が日付つきで載る", "新しいマイク検討中" in base)
check("定番ネタが載る", "黄色いひげ" in base)

# ---- 21. 視聴者反応プロンプトに手帳メモが添えられる ----
gen._profile_manager.add_viewer_note("satoo_1976", "大豆が好き")
gen._profile_manager._profile.setdefault('known_viewers', {}).setdefault(
    'satoo_1976', {'count': 10, 'samples': [], 'notes': [], 'last_seen': ''})
prompt_v = lanes._build_viewer_reaction_prompt("satoo_1976", "こんばんは")
check("視聴者への反応に手帳メモが添えられる", "大豆が好き" in prompt_v)

# ---- 22. 文脈コメント生成に手帳ページが合流する ----
gen5 = CommentGenerator(cfg)
captured = []


def _fake_capture(prompt):
    captured.append(prompt)
    return "手帳と連携できてるか確かめる一言、今日も調子いいね！"


gen5._call_gemini = _fake_capture
if gen5._profile_manager:
    gen5._profile_manager.add_glossary_term("ビクロイ", "フォートナイトの勝利のこと")
c5 = gen5.generate_context_comment("・今日もビクロイ取ったぞ")
check("文脈生成に手帳メモが合流する",
      c5 is not None and captured and "【手帳メモ" in captured[0] and "ビクロイ" in captured[0])

# ============================================================
# v4.30 の新機能テスト
# ============================================================

# ---- 23. モデル二段構え：上位失敗→Lite退避 ----
cfg.GEMINI_MODEL = "gemini-2.5-flash-lite"
cfg.GEMINI_MODEL_SMART = "gemini-2.5-flash"
gen6 = CommentGenerator(cfg)
used = []


def _fake_once(prompt, model_name="", **kw):
    used.append(model_name)
    if model_name == "gemini-2.5-flash":
        return None  # 上位モデルが失敗した想定（レート制限・503等）
    return "退避できたよ、これは二段構えのテストコメントだね！"


gen6._call_gemini_once = _fake_once
r6 = gen6._call_gemini("テスト", smart=True)
check("上位モデル失敗→Liteに自動退避",
      r6 is not None and used[0] == "gemini-2.5-flash" and used[1] == "gemini-2.5-flash-lite")
used.clear()
gen6._call_gemini("テスト2", smart=False)
check("相槌系はLiteを直接使う", used and used[0] == "gemini-2.5-flash-lite")

# ---- 24. 会話継続モード（キャッチボール） ----
lanes._context_memo.clear()
lanes._conversation_until = 0.0
lanes._conversation_turns = 0
twitch.sent.clear()
lanes.on_speech("太郎、今日の調子はどう？")  # 1往復目
check("呼びかけ→即応答（1往復目）", len(twitch.sent) == 1 and twitch.sent[0][1] is True)
lanes.on_speech("なるほどね、それで君はどう思う？")  # 窓内→会話の続き
check("30秒以内の続き発言→即応答（2往復目）", len(twitch.sent) == 2 and twitch.sent[1][1] is True)
lanes.on_speech("そうかそうか、面白いこと言うね")  # 3往復目
check("3往復目も即応答", len(twitch.sent) == 3)
check("3往復で会話モードが一区切り", lanes._conversation_until == 0.0)
lanes.on_speech("これは独り言に戻るはずの発言")
check("会話終了後の発言は文脈メモへ", len(twitch.sent) == 3 and len(lanes._context_memo) == 1)

# 窓の期限切れ
lanes._context_memo.clear()
twitch.sent.clear()
lanes.on_speech("太郎、もう一回話そう")
check("新しい呼びかけ→会話再開", len(twitch.sent) == 1)
lanes._conversation_until = time.time() - 1  # 窓を強制的に期限切れにする
lanes.on_speech("時間切れ後の発言だよ")
check("窓が過ぎた発言は文脈メモへ", len(twitch.sent) == 1 and len(lanes._context_memo) == 1)

# ============================================================
# v4.40 マルチAI対応のテスト
# ============================================================
from llm_client import OpenAICompatClient, build_smart_client

# ---- 25. 接続クライアントの基本動作 ----
c0 = OpenAICompatClient("", "", "")
check("URL未設定なら未構成扱い", not c0.is_configured())
c1 = OpenAICompatClient("http://localhost:11434/v1", "", "llama3")
check("ローカルLLMはキー無しでも構成OK", c1.is_configured())

import requests as _req


class _FakeResp:
    status_code = 200
    text = ""

    def json(self):
        return {"choices": [{"message": {"content": "外部AIからの返答だよ、テスト成功だね！"}}]}


class _FakeErr:
    status_code = 401
    text = "Unauthorized"

    def json(self):
        return {}


_orig_post = _req.post
_req.post = lambda *a, **k: _FakeResp()
out = c1.chat("システム", "ユーザー")
check("OpenAI互換APIの応答を取り出せる", out == "外部AIからの返答だよ、テスト成功だね！")
_req.post = lambda *a, **k: _FakeErr()
check("接続エラー時はNone（例外で落ちない）", c1.chat("s", "u") is None)
_req.post = _orig_post

# ---- 26. 接続先の切り替え ----
cfg.SMART_PROVIDER = "gemini"
check("接続先=geminiなら外部クライアントなし", build_smart_client(cfg) is None)
cfg.SMART_PROVIDER = "openai"
cfg.OPENAI_BASE_URL = "https://api.openai.com/v1"
cfg.OPENAI_MODEL = "gpt-4o-mini"
cfg.OPENAI_API_KEY = "sk-test"
check("接続先=openaiで外部クライアント生成", build_smart_client(cfg) is not None)

# ---- 27. 会話が外部AI経由になる＋品質チェック＋退避 ----
gen7 = CommentGenerator(cfg)


class _FakeClient:
    model = "gpt-4o-mini"
    base_url = "https://api.openai.com/v1"

    def chat(self, s, u, **kw):
        return "外部AI経由のコメントだよ、日本語チェックも通るね！"


gen7._external_client_cached = _FakeClient()
r_ok = gen7._call_gemini("プロンプト", smart=True)
check("会話が外部AIで生成される", r_ok == "外部AI経由のコメントだよ、日本語チェックも通るね！")


class _BadClient(_FakeClient):
    def chat(self, s, u, **kw):
        return "死ねとか言う外部AIの返答は絶対に通さないぞ"


class _NoneClient(_FakeClient):
    def chat(self, s, u, **kw):
        return None


gen7._call_gemini_once = lambda p, model_name="", **kw: "Liteに退避した安全なコメントですよ！"
gen7._external_client_cached = _BadClient()
r_bad = gen7._call_gemini("プロンプト", smart=True)
check("外部AIのNGワード出力→破棄してLiteに退避", r_bad == "Liteに退避した安全なコメントですよ！")
gen7._external_client_cached = _NoneClient()
r_none = gen7._call_gemini("プロンプト", smart=True)
check("外部AI接続失敗→Liteに退避", r_none == "Liteに退避した安全なコメントですよ！")
cfg.SMART_PROVIDER = "gemini"

# ============================================================
# v4.41 尻切れ対策のテスト
# ============================================================
gen8 = CommentGenerator(cfg)
check("尻切れ文（実戦の実例）を破棄",
      gen8._postprocess_comment("ごめんごめん、オーブガンて、そんな") is None)
check("尻切れ文（実戦の実例2）を破棄",
      gen8._postprocess_comment("うん、即時性上がったのはマジで助") is None)
check("完結した文は通す",
      gen8._postprocess_comment("ごめんごめん、それはオウム返しだったね！") is not None)
check("会話用モデルは大きい出力予算(2048)", gen8._max_tokens_for("gemini-2.5-flash") == 2048)
check("相槌用モデルは従来の予算(300)", gen8._max_tokens_for("gemini-2.5-flash-lite") == 300)

ok_direct, q = lanes._detect_direct_call("コメント太郎は次の話を聞いてください")
check("「太郎は〜」の助詞を除去して呼びかけ検出", ok_direct and q.startswith("次の話"))

# ---- v4.42: 俳句・謎かけは尻切れ検問を免除 ----
check("俳句（句点なし）はrequire_ending=Falseで通す",
      gen8._postprocess_comment("折れた棒　ログ取り終えれば　また次へ", require_ending=False) is not None)
check("俳句も通常経路（require_ending=True）なら破棄される",
      gen8._postprocess_comment("折れた棒　ログ取り終えれば　また次へ") is None)
ok_no, q_no = lanes._detect_direct_call("太郎の抽出はどうだい?")
check("「太郎の〜」の助詞を除去して呼びかけ検出", ok_no and q_no.startswith("抽出"))

# ============================================================
# v4.43 訂正辞書のテスト
# ============================================================
pm3 = PM2('/tmp/pm2')
pm3.add_correction("5変換", "誤変換")
pm3.add_correction("おりしんぱす", "オリジンパス")
pm3.add_correction("x", "y")          # 短すぎる→弾かれる
pm3.add_correction("同じ", "同じ")     # 誤と正が同じ→弾かれる
corr = pm3.get_corrections()
check("訂正ペアが手帳に記録される",
      corr.get("5変換") == "誤変換" and corr.get("おりしんぱす") == "オリジンパス")
check("不正なペアは弾かれる", "x" not in corr and "同じ" not in corr)
check("正しい語がWhisperヒントにも合流", "オリジンパス" in pm3.get_whisper_terms())

am2 = AudioModule(cfg)
am2.set_corrections(corr)
fixed = am2._apply_corrections("それは5変換ですよっておりしんぱすが言ってた")
check("認識結果の誤変換が自動補正される",
      fixed == "それは誤変換ですよってオリジンパスが言ってた")
check("該当なしの文はそのまま", am2._apply_corrections("普通の文です") == "普通の文です")

# v2手帳（corrections欄なし）を読んでも壊れない
pm4 = PM2('/tmp/pm2')
check("既存手帳にcorrections欄がなくても動く", isinstance(pm4.get_corrections(), dict))

# ---- v4.43: 視聴者をIDではなく呼び名で呼ぶ ----
# lanesのgenは実プロフィール（作業ディレクトリの空手帳）を持つので呼び名を仕込む
gen._profile_manager._profile.setdefault('viewer_names', {})['turbo35gtr'] = 'ターボさん'
gen._profile_manager._profile.setdefault('known_viewers', {}).setdefault(
    'turbo35gtr', {'count': 30, 'samples': [], 'notes': [], 'last_seen': ''})
p_named = lanes._build_viewer_reaction_prompt('turbo35gtr', 'こんばんは')
check("呼び名登録済み→呼び名で呼ぶ指示が入る",
      '「ターボさん」と呼ぶこと' in p_named)
gen._profile_manager._profile['known_viewers']['nanashi_123'] = {
    'count': 3, 'samples': [], 'notes': [], 'last_seen': ''}
p_noname = lanes._build_viewer_reaction_prompt('nanashi_123', 'よろしく')
check("呼び名未設定→IDを呼び名にしない指示が入る",
      'そのまま呼び名にしない' in p_noname)
p_first = lanes._build_viewer_reaction_prompt('shinjin_999', '初見です')
check("初見さん→はじめましての指示が入る", 'はじめまして' in p_first)

# ============================================================
# v4.50 取材・覚えて・検索・表示名のテスト
# ============================================================
import json as _j

# ---- 取材モード ----
cfg.INTERVIEW_ENABLED = True
lanes._interview = None
lanes._interviewed = set()
lanes._interview_count = 0
lanes._last_comment_time = 0.0
lanes._last_chokkai_time = time.time()  # ちょっかいは封じる
twitch.sent.clear()
gen._call_gemini_raw = lambda p, **kw: "ぺち"
lanes.on_viewer_comment("こんにちは、初見です", "petil_momokira", display_name="桃煌ぺてぃる")
check("未設定さんに取材質問（Twitch表示名で呼ぶ）",
      any("桃煌ぺてぃる" in m and "お呼びすれば" in m for m, _ in twitch.sent))
lanes.on_viewer_comment("ぺちでいいよ〜", "petil_momokira", display_name="桃煌ぺてぃる")
check("本人の答えから呼び名を保存",
      gen._profile_manager._profile['viewer_names'].get("petil_momokira") == "ぺち")
check("復唱の確認コメントが出る", any("ぺちさんですね" in m for m, _ in twitch.sent))
check("取材が終了する", lanes._interview is None)
twitch.sent.clear()
lanes._last_comment_time = 0.0
lanes.on_viewer_comment("もう一回きたよ", "petil_momokira", display_name="桃煌ぺてぃる")
check("同じ人に二度は聞かない（設定済みになった）",
      not any("お呼びすれば" in m for m, _ in twitch.sent))
cfg.INTERVIEW_ENABLED = False

# ---- 覚えてコマンド ----
gen._call_gemini_raw = lambda p, **kw: _j.dumps(
    {"kind": "correction", "wrong": "5変換", "right": "誤変換",
     "confirm": "覚えたよ！「5変換」は「誤変換」の聞き間違いね！"}, ensure_ascii=False)
gen._conversation_history.append({"role": "streamer", "content": "5変換じゃなくて誤変換だよ"})
twitch.sent.clear()
lanes._conversation_until = 0.0
lanes.on_speech("太郎、今の覚えて")
check("覚えてコマンド→訂正が手帳に入る",
      gen._profile_manager.get_corrections().get("5変換") == "誤変換")
check("覚えた内容を復唱する", any("覚えたよ" in m for m, _ in twitch.sent))
check("訂正が耳にも即反映される", audio.corrections.get("5変換") == "誤変換")

# ---- 取り消し ----
twitch.sent.clear()
lanes._conversation_until = 0.0
lanes.on_speech("太郎、さっきの登録しないで")
check("取り消しで直前の記録が消える", "5変換" not in gen._profile_manager.get_corrections())
check("取り消しの確認コメントが出る", any("取り消し" in m for m, _ in twitch.sent))

# ---- 検索機能 ----
cfg.SEARCH_ENABLED = True
gen.generate_search_answer = lambda q: "検索したよ！新シーズンは今週開始だって、公式情報ね！"
twitch.sent.clear()
lanes._conversation_until = 0.0
lanes.on_speech("太郎、フォートナイトの新シーズンについて調べて")
check("「調べて」→検索回答を優先送信",
      twitch.sent and "検索したよ" in twitch.sent[0][0] and twitch.sent[0][1] is True)
gen.generate_search_answer = lambda q: None  # 検索失敗の想定
twitch.sent.clear()
lanes._conversation_until = 0.0
lanes.on_speech("太郎、変なワードについて調べて")
check("検索失敗→通常会話に退避して答える", len(twitch.sent) == 1)
cfg.SEARCH_ENABLED = False
twitch.sent.clear()
lanes._conversation_until = 0.0
lanes.on_speech("太郎、これについて調べて")
check("検索OFF時は通常会話で返答", len(twitch.sent) == 1)
cfg.SEARCH_ENABLED = True

# ---- 表示名で呼ぶ（呼び名未設定でもTwitch表示名があればそれを使う） ----
lanes._last_chokkai_time = time.time()
lanes.on_viewer_comment("よろしくです", "momo_tester", display_name="モモたん")
p_disp = lanes._build_viewer_reaction_prompt("momo_tester", "やほー")
check("呼び名未設定でもTwitch表示名で呼ぶ", "「モモたん」と呼ぶこと" in p_disp)

# ============================================================
# v4.51 俳句イベントの注釈漏れ対策のテスト
# ============================================================
gen9 = CommentGenerator(cfg)
check("俳句のAI注釈漏れ（実戦の実例）を破棄",
      gen9._postprocess_comment(
          "夜霧舞い 昔話に 花も咲く （※これは例であり、実際にはこの俳句を投稿しません）",
          require_ending=False) is None)
check("「投稿しません」を含む出力を破棄",
      gen9._postprocess_comment(
          "この俳句を投稿しませんという注釈付きの変な出力です",
          require_ending=False) is None)
check("普通の俳句はこれまで通り通す",
      gen9._postprocess_comment("夏の夜に　建築バトルの　音響く", require_ending=False) is not None)
check("「実際には」を含む普通の会話は誤爆しない",
      gen9._postprocess_comment("実際にはそんなに強くない武器だったよね！") is not None)

# ============================================================
# v4.53 ギミック参加のテスト
# ============================================================
from twitch_module import TwitchModule

cfg_g = FakeConfig()
cfg_g.GIMMICK_ENABLED = True
cfg_g.GIMMICK_WORDS = "行進,ランダム,おなかすいた"
cfg_g.GIMMICK_ANNOUNCER_ACCOUNTS = "nightbot"
cfg_g.GIMMICK_DELAY_MIN = 0
cfg_g.GIMMICK_DELAY_MAX = 0
tm = TwitchModule(cfg_g)

check("告知にギミック単語→検知する",
      tm.check_gimmick("nightbot", "チャット欄に「行進」と入力するとスタンプが行進します。") == "行進")
check("ギミック単語なしの告知→検知しない",
      tm.check_gimmick("nightbot", "チャンネルポイントにはチンチロがあります") is None)
check("告知アカウント以外の発言→検知しない",
      tm.check_gimmick("turbo35gtr", "行進") is None)
check("大文字小文字が違っても告知アカウントを認識する",
      tm.check_gimmick("NightBot", "「ランダム」といれると賑やかになります") == "ランダム")

tm.schedule_gimmick("行進")
check("投稿が予約される", len(tm._gimmick_pending) == 1)
check("時間が来たら取り出せる（遅延0秒）", tm.pop_due_gimmick() == "行進")
check("取り出した後は空", tm.pop_due_gimmick() is None)

cfg_g.GIMMICK_ENABLED = False
check("機能OFFなら検知しない",
      tm.check_gimmick("nightbot", "「行進」と入力するとスタンプが行進します") is None)

print()
ok = sum(1 for _, c in results if c)
print(f"===== 結果: {ok}/{len(results)} 件成功 =====")
sys.exit(0 if ok == len(results) else 1)
