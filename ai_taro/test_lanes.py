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

# ============================================================
# v4.54 音声トリガーのギミック（ビクロイ→gg）のテスト
# ============================================================
cfg_sg = FakeConfig()
cfg_sg.SPEECH_GIMMICKS = "ビクロイ=gg"
cfg_sg.SPEECH_GIMMICK_COOLDOWN = 120
gen_sg = CommentGenerator(cfg_sg)
twitch_sg = FakeTwitch()
lanes_sg = LaneManager(cfg_sg, gen_sg, twitch_sg, FakeAudio())

lanes_sg.on_speech("よし、ビクロイ取れたよ！")
check("「ビクロイ」を聞いたらggを投稿", any(m == "gg" for m, _ in twitch_sg.sent))
lanes_sg.on_speech("またビクロイだ！")
check("クールダウン中は連発しない", [m for m, _ in twitch_sg.sent].count("gg") == 1)

twitch_sg.sent.clear()
lanes_sg._speech_gimmick_times.clear()
lanes_sg.on_speech("ビクロイ取れなかったー")
check("否定的な文では出さない", not any(m == "gg" for m, _ in twitch_sg.sent))
lanes_sg.on_speech("今日は普通の話をしています")
check("トリガー語がなければ何もしない", not any(m == "gg" for m, _ in twitch_sg.sent))

lanes_sg._speech_gimmick_times.clear()
lanes_sg.on_speech("見事にビクロイ、素晴らしい！")
check("クールダウンが明ければまた出せる", any(m == "gg" for m, _ in twitch_sg.sent))

# ============================================================
# v4.55 聞き返しオウム検問・冒頭名検問のテスト（7/17配信の実例から）
# ============================================================
gen10 = CommentGenerator(cfg)

check("カタカナ語＋って何？を破棄（実例1）",
      gen10._postprocess_comment("え、トリアーゼロクロックって何？なんか新しい技とか出るのかな。") is None)
check("カタカナ語＋って何？を破棄（実例2）",
      gen10._postprocess_comment("スライカーポンプって何？　なんか新しいゲーム用語かな、俺も勉強しないとだめだね！") is None)
check("かぎ括弧引用＋って何を破棄（実例3）",
      gen10._postprocess_comment("「アイアバス」って何かのアイテム名かな？　なんか響きが面白くて気になるよ。") is None)
check("かぎ括弧引用＋どういうことを破棄",
      gen10._postprocess_comment("「おしだけめったうち」って、どういうこと？敵が隠れてたのかな？") is None)
check("普通の感想コメントは通す",
      gen10._postprocess_comment("そのアイテムの使い方、初めて見たかも。勉強になるなあ。") is not None)
check("引用しない普通の質問は通す",
      gen10._postprocess_comment("敵はどっちから来たの？結構危なかったよね。") is not None)

check("冒頭の「ひげさん、」を取り除く",
      gen10._postprocess_comment("ひげさん、今日のプレイは冴えてるね！最高だったよ。")
      == "今日のプレイは冴えてるね！最高だったよ。")
check("文中の名前はそのまま",
      gen10._postprocess_comment("今日のひげさん、なんだか調子良さそうだね！")
      == "今日のひげさん、なんだか調子良さそうだね！")

# ============================================================
# v4.57 読み上げ辞書（reading_dictionary）のテスト
# ============================================================
import reading_dictionary as rd

_RD_TEST_DIR = '/tmp/taro_test_reading_dict'
shutil.rmtree(_RD_TEST_DIR, ignore_errors=True)
os.makedirs(_RD_TEST_DIR, exist_ok=True)

rd_data = rd.load_dictionary(_RD_TEST_DIR)
check("初回読み込みで辞書ファイルが作られる",
      os.path.exists(os.path.join(_RD_TEST_DIR, rd.DICT_FILE)))
check("初期データに棒読みちゃん追加語が入っている", rd_data["words"].get("桃煌") == "ももきら")
check("初期データにTTAのチャンネル絵文字が入っている", rd_data["words"].get("higereGg") == "GG")

check("単純な置き換え（草→くさ）", rd.apply_reading("草生える", rd_data) == "くさ生える")
check("チャンネル絵文字の置き換え",
      rd.apply_reading("higereGgしたね", rd_data) == "GGしたね")
check("wwwwの連続をわらわらに", rd.apply_reading("それなwwww", rd_data) == "それなわらわら")
check("URLをゆーあーるえるに",
      rd.apply_reading("見て https://example.com/path すごい", rd_data)
      == "見て ゆーあーるえる すごい")
check("辞書にない語はそのまま", rd.apply_reading("こんにちは", rd_data) == "こんにちは")
check("空文字はそのまま", rd.apply_reading("", rd_data) == "")

rd2 = rd.add_word(rd_data, "テスト用語", "てすとようご")
check("覚えて相当の追加ができる", rd2["words"]["テスト用語"] == "てすとようご")
check("追加した語がすぐ読み上げに反映される",
      rd.apply_reading("テスト用語です", rd2) == "てすとようごです")

rd.save_dictionary(rd_data, _RD_TEST_DIR)
rd_reloaded = rd.load_dictionary(_RD_TEST_DIR)
check("保存して読み直しても内容が保たれる",
      rd_reloaded["words"].get("テスト用語") == "てすとようご")

# 壊れたJSONでも落ちずに初期データへ復旧する
_broken_path = os.path.join(_RD_TEST_DIR, rd.DICT_FILE)
with open(_broken_path, "w", encoding="utf-8") as _f:
    _f.write("{壊れたJSON")
rd_recovered = rd.load_dictionary(_RD_TEST_DIR)
check("壊れたJSONでも初期データで復旧する", rd_recovered["words"].get("草") == "くさ")

# ============================================================
# v4.57 読み上げ除外リスト（reading_exclusions）のテスト
# ============================================================
import reading_exclusions as re_mod

_RE_TEST_DIR = '/tmp/taro_test_reading_exclusions'
shutil.rmtree(_RE_TEST_DIR, ignore_errors=True)
os.makedirs(_RE_TEST_DIR, exist_ok=True)

re_data = re_mod.load_exclusions(_RE_TEST_DIR)
check("初回読み込みで除外リストファイルが作られる",
      os.path.exists(os.path.join(_RE_TEST_DIR, re_mod.EXCLUSIONS_FILE)))
check("初期データにTTAで確認した4人が入っている",
      set(re_data["excluded_users"]) == {"wizebot", "higeremu_translate", "sery_bot", "higeremu_tr"})

check("除外対象は読まない", re_mod.should_read("higeremu_tr", re_data) is False)
check("大文字小文字を区別しない", re_mod.should_read("HIGEREMU_TR", re_data) is False)
check("除外対象でなければ読む", re_mod.should_read("petil_momokira", re_data) is True)
check("空のユーザー名は読む扱い", re_mod.should_read("", re_data) is True)

re_mod.add_exclusion(re_data, "test_bad_bot")
check("除外を追加できる", re_mod.should_read("test_bad_bot", re_data) is False)
re_mod.add_exclusion(re_data, "TEST_BAD_BOT")
check("同じ人を大文字小文字違いで二重追加しない",
      len([u for u in re_data["excluded_users"] if u.lower() == "test_bad_bot"]) == 1)

re_mod.remove_exclusion(re_data, "wizebot")
check("除外を解除できる", re_mod.should_read("wizebot", re_data) is True)

re_mod.save_exclusions(re_data, _RE_TEST_DIR)
re_reloaded = re_mod.load_exclusions(_RE_TEST_DIR)
check("保存して読み直しても内容が保たれる",
      "test_bad_bot" in [u.lower() for u in re_reloaded["excluded_users"]]
      and "wizebot" not in [u.lower() for u in re_reloaded["excluded_users"]])

# ============================================================
# v4.57 VOICEVOXクライアント（voicevox_client）のテスト
# ネットワークには一切繋がず、requests互換のフェイクセッションでモックする
# ============================================================
import voicevox_client as vv


class _FakeResp:
    def __init__(self, status_code=200, json_data=None, content=b""):
        self.status_code = status_code
        self._json_data = json_data
        self.content = content
        self.text = str(json_data) if json_data is not None else ""

    def json(self):
        return self._json_data


class _FakeSession:
    """呼ばれた回数・引数を記録しつつ、あらかじめ用意した応答を順に返す"""
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self._responses.pop(0)

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self._responses.pop(0)


vv_ok_session = _FakeSession([
    _FakeResp(200, json_data={"accent_phrases": []}),
    _FakeResp(200, content=b"RIFF....WAVEfake"),
])
vv_wav = vv.synthesize("こんにちは", session=vv_ok_session)
check("VOICEVOX合成が成功するとWAVバイト列が返る", vv_wav == b"RIFF....WAVEfake")
check("audio_query→synthesisの順で2回呼ばれる",
      [c[0] + " " + c[1].split("/")[-1] for c in vv_ok_session.calls]
      == ["POST audio_query", "POST synthesis"])
check("話者IDが青山龍星ノーマル(13)で渡される",
      vv_ok_session.calls[0][2]["params"]["speaker"] == 13)

check("空文字は接続せずNoneを返す", vv.synthesize("", session=_FakeSession([])) is None)

vv_query_fail_session = _FakeSession([_FakeResp(500)])
check("audio_query失敗時はNone",
      vv.synthesize("テスト", session=vv_query_fail_session) is None)

vv_synth_fail_session = _FakeSession([
    _FakeResp(200, json_data={"accent_phrases": []}),
    _FakeResp(500),
])
check("synthesis失敗時はNone",
      vv.synthesize("テスト", session=vv_synth_fail_session) is None)


class _RaisingSession:
    def post(self, url, **kwargs):
        raise ConnectionError("engine not running")

    def get(self, url, **kwargs):
        raise ConnectionError("engine not running")


check("エンジン未起動（例外）でも落ちずにNone",
      vv.synthesize("テスト", session=_RaisingSession()) is None)
check("is_engine_running: 応答200ならTrue",
      vv.is_engine_running(session=_FakeSession([_FakeResp(200)])) is True)
check("is_engine_running: 例外ならFalse",
      vv.is_engine_running(session=_RaisingSession()) is False)

# ============================================================
# v4.58 Gemini TTSクライアント（gemini_tts_client・HTTP直接）のテスト
# ネットワークには繋がず、requests互換のフェイクセッション（_FakeSession）でモックする
# ============================================================
import base64 as _b64
import gemini_tts_client as gtts


def _gemini_ok(wav=b"RIFFfakewav"):
    return _FakeResp(200, json_data={"candidates": [{"content": {"parts": [
        {"inlineData": {"mimeType": "audio/wav", "data": _b64.b64encode(wav).decode()}}]}}]})


gs = _FakeSession([_gemini_ok()])
check("Gemini TTS合成が成功するとWAVバイト列が返る",
      gtts.synthesize("Hello", api_key="dummy", session=gs) == b"RIFFfakewav")
_body = gs.calls[0][2]["json"]
check("既定モデルはLite", gs.calls[0][1].endswith("/gemini-3.8-flash-lite-tts:generateContent"))
check("声の名前が渡る",
      _body["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Kore")
check("口調指定なしなら本文だけ（別枠は付かない）", _body["contents"][0]["parts"][0] == {"text": "Hello"})
check("言語指定なしなら自動判定（languageCodeを付けない）",
      "languageCode" not in _body["generationConfig"]["speechConfig"])

gs2 = _FakeSession([_gemini_ok()])
gtts.synthesize("ここで一句", api_key="dummy", style="生意気に", language_code="ja-JP", session=gs2)
_p = gs2.calls[0][2]["json"]["contents"][0]["parts"][0]
check("口調は本文とは別枠（speech_metadata.style）で渡す＝読み上げられない",
      _p["text"] == "ここで一句" and _p["speech_metadata"] == {"style": "生意気に"})
check("口調の指示文が本文に混ざらない", "生意気" not in _p["text"])
check("言語を固定できる（ja-JP）",
      gs2.calls[0][2]["json"]["generationConfig"]["speechConfig"]["languageCode"] == "ja-JP")

check("空文字は接続せずNoneを返す", gtts.synthesize("", api_key="dummy", session=_FakeSession([])) is None)
check("APIキー未設定ならNone", gtts.synthesize("Hello", api_key="", session=_FakeSession([])) is None)
check("1分あたりの上限（429）ならNone",
      gtts.synthesize("Hello", api_key="dummy", session=_FakeSession([_FakeResp(429, json_data={})])) is None)

# v4.59 1分あたりの回数を数える
gtts._reset_rate_state()
gtts.set_rate_limit("m-test", 2)
_rs = _FakeSession([_gemini_ok(), _gemini_ok(), _gemini_ok()])
_r1 = gtts.synthesize("a", api_key="k", model="m-test", session=_rs)
_r2 = gtts.synthesize("b", api_key="k", model="m-test", session=_rs)
_r3 = gtts.synthesize("c", api_key="k", model="m-test", session=_rs)
check("1分の回数: 決めた回数までは使う", _r1 and _r2 and gtts.calls_last_minute("m-test") == 2)
check("1分の回数: 超えそうなら頼まずに見送る（Geminiに断られる前に止める）",
      _r3 is None and len(_rs.calls) == 2)
check("1分の回数: 制限のないモデルは数えるだけで止めない",
      gtts.synthesize("d", api_key="k", model="free", session=_FakeSession([_gemini_ok()])) == b"RIFFfakewav")
gtts._reset_rate_state()
_rs429 = _FakeSession([_FakeResp(429, json_data={"error": {"details": [{"retryDelay": "27s"}]}}), _gemini_ok()])
gtts.synthesize("e", api_key="k", model="m429", session=_rs429)
check("429で言われた待ち時間の間は、そのモデルを使わない",
      gtts.synthesize("f", api_key="k", model="m429", session=_rs429) is None and len(_rs429.calls) == 1)
gtts._reset_rate_state()

check("candidatesが空ならNone",
      gtts.synthesize("Hello", api_key="dummy",
                      session=_FakeSession([_FakeResp(200, json_data={"candidates": []})])) is None)
check("音声データが無ければNone",
      gtts.synthesize("Hello", api_key="dummy", session=_FakeSession(
          [_FakeResp(200, json_data={"candidates": [{"content": {"parts": [{"text": "x"}]}}]})])) is None)
check("API接続失敗（例外）でも落ちずにNone",
      gtts.synthesize("Hello", api_key="dummy", session=_RaisingSession()) is None)

# ============================================================
# v4.58 太郎の声（speak_as_taro・読み替え）のテスト
# ============================================================
check("読み替え: ひげさん→ヒゲさん",
      gtts.apply_replacements("ひげさん下手すぎ", "ひげさん=ヒゲさん") == "ヒゲさん下手すぎ")
check("読み替えは複数指定できる",
      gtts.apply_replacements("ひげさんとGG", "ひげさん=ヒゲさん, GG=ジージー") == "ヒゲさんとジージー")
check("読み替え指定なしならそのまま", gtts.apply_replacements("ひげさん", "") == "ひげさん")

gs3 = _FakeSession([_gemini_ok(b"taro-voice")])
check("太郎の声として合成できる",
      gtts.speak_as_taro("ひげさん、今の一句", api_key="dummy", style="生意気に",
                         model="gemini-3.8-flash-tts", voice_name="Algieba",
                         replacements="ひげさん=ヒゲさん", session=gs3) == b"taro-voice")
_tb = gs3.calls[0][2]["json"]
check("太郎の声: 読み替え後の文を送る（チャットの文は変えない）",
      _tb["contents"][0]["parts"][0]["text"] == "ヒゲさん、今の一句")
check("太郎の声: 既定で日本語固定", _tb["generationConfig"]["speechConfig"]["languageCode"] == "ja-JP")
check("太郎の声: 上位版モデルとAlgiebaが使われる",
      gs3.calls[0][1].endswith("/gemini-3.8-flash-tts:generateContent")
      and _tb["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Algieba")

# ============================================================
# v4.57 読み上げパイプライン（reading_pipeline）のテスト
# 外の世界（VOICEVOX/Gemini）は全部フェイク関数で差し替える
# ============================================================
import reading_pipeline as rp

check("日本語（かな）を検出", rp.detect_language("こんにちは") == "ja")
check("日本語（漢字のみ）を検出", rp.detect_language("配信中") == "ja")
check("英語を検出", rp.detect_language("hello there") == "en")
check("記号や数字だけは英語扱い", rp.detect_language("!!! 123") == "en")
check("日英混在は日本語扱い（仕様どおり）", rp.detect_language("nice play だね") == "ja")

_rp_dict = rd._seed_dictionary()
_rp_excl = re_mod._seed_exclusions()


def _fake_vv_ok(text, **kwargs):
    return f"VV:{text}".encode()


def _fake_vv_none(text, **kwargs):
    return None


def _fake_gemini_ok(text, **kwargs):
    return f"GEMINI:{text}".encode()


def _fake_gemini_fail(text, **kwargs):
    return None


check("除外ユーザーは読まない",
      rp.read_comment("higeremu_tr", "こんにちは", gemini_api_key="x",
                       dictionary_data=_rp_dict, exclusions_data=_rp_excl,
                       voicevox_synthesize=_fake_vv_ok, gemini_synthesize=_fake_gemini_ok)
      is None)

check("日本語コメントはVOICEVOXで読む",
      rp.read_comment("viewer1", "こんにちは", gemini_api_key="x",
                       dictionary_data=_rp_dict, exclusions_data=_rp_excl,
                       voicevox_synthesize=_fake_vv_ok, gemini_synthesize=_fake_gemini_ok)
      == "VV:こんにちは".encode())

check("英語コメントはGemini TTSで読む",
      rp.read_comment("viewer1", "hello world", gemini_api_key="x",
                       dictionary_data=_rp_dict, exclusions_data=_rp_excl,
                       voicevox_synthesize=_fake_vv_ok, gemini_synthesize=_fake_gemini_ok)
      == b"GEMINI:hello world")

check("英語でGeminiが落ちたらVOICEVOXにフォールバック（決定事項）",
      rp.read_comment("viewer1", "hello world", gemini_api_key="x",
                       dictionary_data=_rp_dict, exclusions_data=_rp_excl,
                       voicevox_synthesize=_fake_vv_ok, gemini_synthesize=_fake_gemini_fail)
      == b"VV:hello world")

check("辞書を通してから読む（草→くさ）",
      rp.read_comment("viewer1", "草", gemini_api_key="x",
                       dictionary_data=_rp_dict, exclusions_data=_rp_excl,
                       voicevox_synthesize=_fake_vv_ok, gemini_synthesize=_fake_gemini_ok)
      == "VV:くさ".encode())

check("辞書適用後に空になったら読まない",
      rp.read_comment("viewer1", "", gemini_api_key="x",
                       dictionary_data=_rp_dict, exclusions_data=_rp_excl,
                       voicevox_synthesize=_fake_vv_ok, gemini_synthesize=_fake_gemini_ok)
      is None)

check("VOICEVOXも落ちていれば全体としてNone",
      rp.read_comment("viewer1", "こんにちは", gemini_api_key="x",
                       dictionary_data=_rp_dict, exclusions_data=_rp_excl,
                       voicevox_synthesize=_fake_vv_none, gemini_synthesize=_fake_gemini_ok)
      is None)

# ============================================================
# v4.57 読み上げの配線（エモート除去・文字数制限・略語・読み上げ係）のテスト
# ============================================================
check("エモート名は読まずに取り除く",
      rp.strip_emotes("nice Kappa play", ["Kappa"]) == "nice play")
check("辞書にあるチャンネル絵文字は残して辞書で読ませる",
      rp.strip_emotes("higereGg Kappa", ["higereGg", "Kappa"], keep_words=["higereGg"]) == "higereGg")
check("エモートが無ければそのまま", rp.strip_emotes("こんにちは", None) == "こんにちは")
check("日本語は30文字を超えたら以下略",
      rp.truncate_japanese("あ" * 40, 30) == "あ" * 30 + "、以下略")
check("30文字以内はそのまま", rp.truncate_japanese("こんにちは", 30) == "こんにちは")

_d = rd._seed_dictionary()
check("略語brbを言い換える", rd.apply_reading("brb guys", _d) == "be right back guys")
check("略語は大文字でも言い換える", rd.apply_reading("NGL that was good", _d) == "not gonna lie that was good")
check("ggはそのまま（おじさん指定）", rd.apply_reading("gg", _d) == "gg")
check("単語の一部（eggのgg・wpを含む語）は置き換えない",
      rd.apply_reading("egg wpx", _d) == "egg wpx")
check("gg wp → gg well played", rd.apply_reading("gg wp", _d) == "gg well played")

import twitch_module as _tm_mod
check("Twitchのemotesタグからエモート名を取り出す",
      sorted(_tm_mod.TwitchModule.extract_emote_names("Kappa hi Kappa Keepo",
                                                      {"emotes": "25:0-4,9-13/1902:15-19"}))
      == ["Kappa", "Keepo"])
check("emotesタグが無ければ空", _tm_mod.TwitchModule.extract_emote_names("hi", {}) == [])

import read_aloud as ra_mod


class _RAConfig:
    READ_ALOUD_MAX_QUEUE = 2
    READ_ALOUD_MAX_CHARS_JA = 30
    READ_ALOUD_ENGLISH_VOICE = "Puck"
    VOICEVOX_ENGINE_PATH = ""


_played = []
_pipeline_calls = []


def _fake_pipeline(username, text, **kwargs):
    _pipeline_calls.append((username, text, kwargs))
    return f"WAV:{text}".encode()


_ra_dir = '/tmp/taro_test_read_aloud'
shutil.rmtree(_ra_dir, ignore_errors=True)
os.makedirs(_ra_dir, exist_ok=True)
worker = ra_mod.ReadAloudWorker(_RAConfig(), gemini_api_key="x", base_dir=_ra_dir,
                                play_func=_played.append, pipeline_func=_fake_pipeline,
                                engine_check=lambda: True)
worker.start()
worker.enqueue_comment("viewer1", "こんにちは", ["Kappa"])
worker.enqueue_wav(b"TARO-VOICE")
for _ in range(50):
    if len(_played) >= 2:
        break
    time.sleep(0.05)
check("読み上げ係がコメントを合成して再生する", _played[:1] == ["WAV:こんにちは".encode()])
check("合成済み音声（太郎の声）も同じ列で再生する", b"TARO-VOICE" in _played)
check("エモート名と文字数制限が合成に渡る",
      _pipeline_calls[0][2]["emote_names"] == ["Kappa"] and _pipeline_calls[0][2]["max_chars_ja"] == 30)
check("英語の声は設定の値（Puck）", worker.english_voice == "Puck")
worker.stop()
worker.stop()
check("停止を2回呼んでも落ちない", True)
check("エンジンが既に動いていれば太郎は起動しない（止めもしない）", worker._engine_proc is None)

# 列が詰まったら捨てる（スレッドを動かさずに確認）
worker2 = ra_mod.ReadAloudWorker(_RAConfig(), base_dir=_ra_dir, play_func=_played.append,
                                 pipeline_func=_fake_pipeline, engine_check=lambda: True)
worker2.enqueue_comment("a", "1")
worker2.enqueue_comment("b", "2")
worker2.enqueue_comment("c", "3")
check("読み上げ待ちが上限を超えたら新しいコメントは捨てる", worker2._queue.qsize() == 2)

# ============================================================
# v4.57 太郎の声（Gemini）の配線テスト
# ============================================================
class _TaroCfg(_RAConfig):
    TARO_VOICE_ENABLED = True
    TARO_VOICE_NAME = "Algieba"
    TARO_VOICE_MODEL = "gemini-3.8-flash-tts"
    TARO_VOICE_FALLBACK_MODEL = "gemini-3.8-flash-lite-tts"
    TARO_VOICE_STYLE = "生意気に"
    BOT_NICK = "higeremu_ai"
    READ_ALOUD_MAX_QUEUE = 50


def _wait_played(lst, n, sec=5):
    t0 = time.time()
    while len(lst) < n and time.time() - t0 < sec:
        time.sleep(0.02)


_taro_calls = []


def _slow_taro_synth(text, **kw):
    _taro_calls.append((text, kw))
    time.sleep(0.3)  # 声づくりに時間がかかる想定
    return f"GEMINI-TARO:{text}".encode()


_played_t = []
_pipe_t = []


def _pipe_rec(username, text, **kw):
    _pipe_t.append((username, text))
    return f"VV:{username}:{text}".encode()


wt = ra_mod.ReadAloudWorker(_TaroCfg(), gemini_api_key="x", base_dir=_ra_dir,
                            play_func=_played_t.append, pipeline_func=_pipe_rec,
                            engine_check=lambda: True, taro_synth=_slow_taro_synth)
wt.start()
wt.enqueue_taro("ひげさん下手すぎ")
wt.enqueue_comment("viewer1", "こんばんは")
_wait_played(_played_t, 2)
check("太郎の声オン: 太郎の発言はGeminiの声で読む", "GEMINI-TARO:ひげさん下手すぎ".encode() in _played_t)
check("太郎の声に口調・声・モデルが渡る",
      _taro_calls[0][1]["style"] == "生意気に" and _taro_calls[0][1]["voice_name"] == "Algieba"
      and _taro_calls[0][1]["model"] == "gemini-3.8-flash-tts")
check("再生は列の順番どおり（太郎→視聴者）",
      _played_t[:2] == ["GEMINI-TARO:ひげさん下手すぎ".encode(), "VV:viewer1:こんばんは".encode()])
wt.stop()

_played_f = []
wf = ra_mod.ReadAloudWorker(_TaroCfg(), gemini_api_key="x", base_dir=_ra_dir,
                            play_func=_played_f.append, pipeline_func=_pipe_rec,
                            engine_check=lambda: True, taro_synth=lambda text, **kw: None)
wf.start()
wf.enqueue_taro("失敗テスト")
_wait_played(_played_f, 1)
check("Geminiが失敗したら太郎の発言はVOICEVOXで読む（黙らない）",
      _played_f[:1] == ["VV:higeremu_ai:失敗テスト".encode()])
wf.stop()


# v4.59 上位版が上限で断ったら軽量版で作り直す
_fb_models = []


def _flash_fails(text, **kw):
    _fb_models.append(kw["model"])
    return None if kw["model"] == "gemini-3.8-flash-tts" else b"LITE-VOICE"


_played_fb = []
wfb = ra_mod.ReadAloudWorker(_TaroCfg(), gemini_api_key="x", base_dir=_ra_dir,
                             play_func=_played_fb.append, pipeline_func=_pipe_rec,
                             engine_check=lambda: True, taro_synth=_flash_fails)
wfb.normalize_enabled = False
wfb.start()
wfb.enqueue_taro("上限テスト")
_wait_played(_played_fb, 1)
check("上位版が断ったら軽量版で同じ声を作り直す",
      _played_fb[:1] == [b"LITE-VOICE"] and _fb_models == ["gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts"])
wfb.stop()


# 2026-10-01 既定は軽量版。軽量版が断ったら上位版で作り直す
_fb2_models = []


def _lite_fails(text, **kw):
    _fb2_models.append(kw["model"])
    return None if kw["model"] == "gemini-3.8-flash-lite-tts" else b"FLASH-VOICE"


class _LiteDefaultCfg(_RAConfig):
    TARO_VOICE_ENABLED = True
    BOT_NICK = "higeremu_ai"
    READ_ALOUD_MAX_QUEUE = 50


_played_fb2 = []
wfb2 = ra_mod.ReadAloudWorker(_LiteDefaultCfg(), gemini_api_key="x", base_dir=_ra_dir,
                              play_func=_played_fb2.append, pipeline_func=_pipe_rec,
                              engine_check=lambda: True, taro_synth=_lite_fails)
wfb2.normalize_enabled = False
wfb2.start()
wfb2.enqueue_taro("軽量版テスト")
_wait_played(_played_fb2, 1)
check("既定は軽量版で、断られたら上位版で作り直す",
      _played_fb2[:1] == [b"FLASH-VOICE"]
      and _fb2_models == ["gemini-3.8-flash-lite-tts", "gemini-3.8-flash-tts"])
wfb2.stop()

# v4.59 太郎の発言をVCにも流す（VCモード中だけ・太郎の発言だけ・音量はVC用だけ小さく）
import io as _io, wave as _wave, struct as _struct


def _make_wav(amp=10000, n=800):
    b = _io.BytesIO()
    with _wave.open(b, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
        w.writeframes(_struct.pack("<%dh" % n, *([amp] * n)))
    return b.getvalue()


def _peak(wav):
    with _wave.open(_io.BytesIO(wav)) as w:
        return max(abs(v) for v in _struct.unpack("<%dh" % w.getnframes(), w.readframes(w.getnframes())))


class _VCOutCfg(_TaroCfg):
    TARO_VC_OUTPUT_DEVICE = "Taro VC"
    TARO_VC_VOLUME = 50


_vc_sent, _main_played = [], []
wvc = ra_mod.ReadAloudWorker(_VCOutCfg(), gemini_api_key="x", base_dir=_ra_dir,
                             play_func=_main_played.append, pipeline_func=lambda *a, **k: _make_wav(),
                             engine_check=lambda: True, taro_synth=lambda text, **kw: _make_wav(),
                             vc_play_func=lambda wav, dev: _vc_sent.append((wav, dev)))
wvc.normalize_enabled = False
wvc.set_taro_vc(False)
wvc.start()
wvc.enqueue_taro("VCオフのとき")
_wait_played(_main_played, 1)
time.sleep(0.2)
check("起動時（スイッチがオフ）は太郎の声をVCに流さない", len(_main_played) == 1 and _vc_sent == [])
wvc.set_taro_vc(True)
wvc.enqueue_comment("viewer1", "視聴者のコメント")
wvc.enqueue_taro("VCオンのとき")
_wait_played(_main_played, 3)
time.sleep(0.3)
check("スイッチがオンなら太郎の発言だけVCに流す（視聴者コメントは流さない）",
      len(_main_played) == 3 and len(_vc_sent) == 1 and _vc_sent[0][1] == "Taro VC")
check("VCに流す分だけ音量を下げる（配信の音はそのまま）",
      _peak(_main_played[-1]) == 10000 and abs(_peak(_vc_sent[0][0]) - 5000) <= 1)
wvc.stop()

_vc_sent2, _main2 = [], []
wvc2 = ra_mod.ReadAloudWorker(_TaroCfg(), gemini_api_key="x", base_dir=_ra_dir,
                              play_func=_main2.append, pipeline_func=_pipe_rec,
                              engine_check=lambda: True, taro_synth=lambda text, **kw: _make_wav(),
                              vc_play_func=lambda wav, dev: _vc_sent2.append(dev))
_ok2 = wvc2.set_taro_vc(True)
wvc2.start()
wvc2.enqueue_taro("出力先なし")
_wait_played(_main2, 1)
time.sleep(0.2)
check("VCの出力先が空欄ならスイッチはオンにならず流さない", _ok2 is False and _vc_sent2 == [])
wvc2.stop()


def _vc_boom(wav, dev):
    raise RuntimeError("出力先が見つかりません")


_main3 = []
wvc3 = ra_mod.ReadAloudWorker(_VCOutCfg(), gemini_api_key="x", base_dir=_ra_dir,
                              play_func=_main3.append, pipeline_func=_pipe_rec,
                              engine_check=lambda: True, taro_synth=lambda text, **kw: _make_wav(),
                              vc_play_func=_vc_boom)
wvc3.set_taro_vc(True)
wvc3.start()
wvc3.enqueue_taro("失敗しても")
wvc3.enqueue_taro("2回目")
_wait_played(_main3, 2)
check("VCに流せなくても配信の太郎の声は止まらない", len(_main3) == 2)
wvc3.stop()

# v4.59 捨てられていたエラー表示（stderr）を記録に流す
import logging as _lg_err
import gui_app as _gui_err
_err_records = []


class _ErrH(_lg_err.Handler):
    def emit(self, record):
        _err_records.append(record.getMessage())


_lg_err.getLogger("stderr_test").addHandler(_ErrH())
_w = _gui_err._StderrToLog("stderr_test")
_w.write("1行目\n2行")
_w.write("目の続き\n\n")
_w.write("最後の行")
_w.flush()
check("stderrへの出力を1行ずつ記録に流す（空行は捨てる）", _err_records == ["1行目", "2行目の続き", "最後の行"])

check("上限エラーは『1分あたり』と分かる形で記録する",
      "1分あたり10回" in gtts._describe_error(_FakeResp(429, json_data={"error": {"details": [
          {"violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel", "quotaValue": "10"}]}]}})))
check("上限エラーは『1日あたり』も見分ける",
      "1日あたり" in gtts._describe_error(_FakeResp(429, json_data={"error": {"details": [
          {"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel", "quotaValue": "100"}]}]}})))


class _TaroOffCfg(_TaroCfg):
    TARO_VOICE_ENABLED = False


_played_o = []
_taro_calls_off = []
wo = ra_mod.ReadAloudWorker(_TaroOffCfg(), gemini_api_key="x", base_dir=_ra_dir,
                            play_func=_played_o.append, pipeline_func=_pipe_rec,
                            engine_check=lambda: True,
                            taro_synth=lambda text, **kw: _taro_calls_off.append(text))
wo.start()
wo.enqueue_taro("オフのとき")
_wait_played(_played_o, 1)
check("太郎の声オフ: 今までどおりVOICEVOXで読み、Geminiは呼ばない",
      _played_o[:1] == ["VV:higeremu_ai:オフのとき".encode()] and _taro_calls_off == [])
wo.stop()

# ============================================================
# v4.57「読み上げだけ」モード（太郎のAIを動かさない日）のテスト
# 画面は開かず、_run_read_only だけを偽物のTwitch・読み上げ係で動かす
# ============================================================
import threading as _th
import types as _types
import logging as _logging
import gui_app as _gui


class _FakeTwitchRO:
    instances = []

    def __init__(self, config):
        self.config = config
        self.started = False
        self.worker = None
        self.sent = []
        _FakeTwitchRO.instances.append(self)

    def set_read_aloud_worker(self, w):
        self.worker = w

    def start(self):
        self.started = True

    def send_comment(self, m):
        self.sent.append(m)


class _FakeWorkerRO:
    def __init__(self, config, gemini_api_key=""):
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        pass


_ro_cfg = _types.SimpleNamespace(CHANNEL_NAME="higeremu", GIMMICK_ENABLED=True, GEMINI_API_KEY="x")
_orig_worker_cls = ra_mod.ReadAloudWorker
ra_mod.ReadAloudWorker = _FakeWorkerRO
_ro_self = _types.SimpleNamespace(bot_running=True, bot_instance=None)
_ro_thread = _th.Thread(target=_gui.BotGUI._run_read_only,
                        args=(_ro_self, _ro_cfg, _FakeTwitchRO, _logging.getLogger("test_ro")),
                        daemon=True)
_ro_thread.start()
time.sleep(0.3)
_tw = _FakeTwitchRO.instances[-1]
check("読み上げだけ: Twitchの受信は始まる", _tw.started)
check("読み上げだけ: 読み上げ係が起動してTwitchにつながる",
      _tw.worker is not None and _tw.worker.started)
check("読み上げだけ: ギミック参加（太郎の投稿）は切られる", _ro_cfg.GIMMICK_ENABLED is False)
check("読み上げだけ: 後片付け用に読み上げ係が登録される",
      _ro_self.bot_instance.get("read_aloud") is _tw.worker and "audio" not in _ro_self.bot_instance)
_ro_self.bot_running = False
_ro_thread.join(timeout=3)
check("読み上げだけ: 停止ボタンで抜ける", not _ro_thread.is_alive())
check("読み上げだけ: 太郎は何も投稿しない", _tw.sent == [])
ra_mod.ReadAloudWorker = _orig_worker_cls

# v4.59 太郎自身の発言は文字数で切らない（視聴者コメントだけ切る）
_mc = []


def _pipe_mc(username, text, **kw):
    _mc.append((username, kw.get("max_chars_ja")))
    return b"x"


for _cfgc, _synth in ((_TaroOffCfg, None), (_TaroCfg, lambda text, **kw: None)):
    _mc.clear()
    _kw = dict(taro_synth=_synth) if _synth else {}
    _w = ra_mod.ReadAloudWorker(_cfgc(), gemini_api_key="x", base_dir=_ra_dir, play_func=lambda w: None,
                                pipeline_func=_pipe_mc, engine_check=lambda: True, **_kw)
    _w.start()
    _ev2 = _th.Event()
    _w.enqueue_taro("太郎の長い発言" * 20)
    _w.enqueue_comment("viewer1", "視聴者の長いコメント" * 20)
    _w.enqueue_done_marker(_ev2)
    _ev2.wait(5)
    _w.stop()
    _label = "声オフ" if _synth is None else "Gemini失敗時"
    check(f"太郎の発言は切らない（{_label}）", ("higeremu_ai", 0) in _mc)
    check(f"視聴者コメントは設定どおり切る（{_label}）", ("viewer1", 30) in _mc)

# v4.58 読み方の指示は口調の後ろに付く
class _PronCfg(_TaroCfg):
    TARO_VOICE_PRONUNCIATION = "「ヒゲさん」は平板で読む"


_wp = ra_mod.ReadAloudWorker(_PronCfg(), base_dir=_ra_dir, play_func=lambda w: None,
                             pipeline_func=_pipe_rec, engine_check=lambda: True)
check("読み方の指示が口調の後ろに付く", _wp.taro_voice_style == "生意気に。「ヒゲさん」は平板で読む")

# v4.58 声の大きさをそろえる（normalize_wav）
import io as _io
import wave as _wave
import numpy as _np


def _make_wav(amp, sec=1.0, rate=24000):
    t = _np.arange(int(rate * sec)) / rate
    y = (amp * _np.sin(2 * _np.pi * 220 * t) * 32767).astype(_np.int16)
    b = _io.BytesIO()
    with _wave.open(b, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(y.tobytes())
    return b.getvalue()


def _level(wav):
    with _wave.open(_io.BytesIO(wav)) as w:
        x = _np.frombuffer(w.readframes(w.getnframes()), dtype=_np.int16).astype(float) / 32768
    v = x[_np.abs(x) > 0.01]
    return 20 * _np.log10(_np.sqrt(_np.mean(v ** 2))), 20 * _np.log10(_np.max(_np.abs(x)))


_loud = ra_mod.normalize_wav(_make_wav(0.8), -21.0)    # Geminiのような大きい声
_quiet = ra_mod.normalize_wav(_make_wav(0.05), -21.0)  # 小さい声
check("大きい声は-21dBFSまで下がる", abs(_level(_loud)[0] - (-21.0)) < 0.5)
check("小さい声は-21dBFSまで上がる", abs(_level(_quiet)[0] - (-21.0)) < 0.5)
check("そろえても割れない（一番大きいところは-3dBFS以下）",
      _level(ra_mod.normalize_wav(_make_wav(0.02), -3.0))[1] <= -2.9)
check("WAVでないデータはそのまま通す", ra_mod.normalize_wav(b"not a wav") == b"not a wav")
check("無音はそのまま", ra_mod.normalize_wav(_make_wav(0.0)) == _make_wav(0.0))

# v4.58 読み上げテストボタン
_played_d = []
wd = ra_mod.ReadAloudWorker(_TaroOffCfg(), gemini_api_key="x", base_dir=_ra_dir,
                            play_func=_played_d.append, pipeline_func=_pipe_rec,
                            engine_check=lambda: True)
wd.start()
_ev = _th.Event()
wd.enqueue_comment("viewer1", "一件目")
wd.enqueue_done_marker(_ev)
check("流し終わったら目印で知らせる", _ev.wait(3) and _played_d == ["VV:viewer1:一件目".encode()])
wd.stop()


class _FakeRoot:
    def after(self, ms, fn, *args):
        fn(*args)


class _FakeBtn:
    def __init__(self):
        self.state = None

    def config(self, **kw):
        self.state = kw.get("state", self.state)


class _FakeRunningWorker:
    taro_voice_enabled = True

    def __init__(self):
        self.items = []

    def enqueue_comment(self, u, t, emotes=None):
        self.items.append(("comment", u, t))

    def enqueue_taro(self, t):
        self.items.append(("taro", t))

    def enqueue_done_marker(self, ev):
        ev.set()


_rw = _FakeRunningWorker()
_logs = []
_ts = _types.SimpleNamespace(bot_running=True, bot_instance={"read_aloud": _rw}, root=_FakeRoot(),
                             read_test_btn=_FakeBtn(), READ_TEST_VIEWER=_gui.BotGUI.READ_TEST_VIEWER,
                             READ_TEST_TARO=_gui.BotGUI.READ_TEST_TARO,
                             _append_log=lambda m, lv="INFO": _logs.append(m))
_gui.BotGUI._read_aloud_test_worker(_ts)
check("起動中の読み上げテスト: 動いている読み上げ係に視聴者コメント→太郎の声の順で並ぶ",
      [i[0] for i in _rw.items] == ["comment", "taro"])
check("読み上げテスト: 終わったらボタンが押せる状態に戻る", _ts.read_test_btn.state == "normal")
check("読み上げテスト: 終わったことを画面の記録に出す", any("終わりました" in m for m in _logs))

# ============================================================
# v4.59 VCモードのテスト（本物の音・Whisper・Geminiは使わない）
# ============================================================
import vc_listener as vcl

_u = vcl.Utterance(threshold=0.01, end_silence_sec=0.3, min_voiced_sec=0.2, max_sec=2.0)
_sil = _np.zeros(1600, dtype=_np.float32)
_loud = (_np.ones(1600, dtype=_np.float32) * 0.1)
check("VC区切り: 静かなままなら何も返さない", all(_u.feed(_sil) is None for _ in range(10)))
_res = [_u.feed(b) for b in [_loud] * 5 + [_sil] * 3]
check("VC区切り: 話して静かになったら1発言として返す",
      _res[-1] is not None and len(_res[-1]) == 1600 * 8 and all(r is None for r in _res[:-1]))
_res2 = [_u.feed(b) for b in [_loud] + [_sil] * 3]
check("VC区切り: 短すぎる物音は発言にしない", all(r is None for r in _res2))
_res3 = [_u.feed(_loud) for _ in range(20)]
check("VC区切り: 長すぎる発言は途中で区切る（最大2秒）", _res3[19] is not None)


class _FakeRecorder:
    def __init__(self, blocks):
        self.blocks = list(blocks)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def record(self, numframes):
        if self.blocks:
            return self.blocks.pop(0).reshape(-1, 1)
        time.sleep(0.02)
        return _np.zeros((numframes, 1), dtype=_np.float32)


class _FakeDev:
    name = "Voice Chat (テスト)"

    def __init__(self, blocks):
        self._blocks = blocks

    def recorder(self, samplerate, channels):
        return _FakeRecorder(self._blocks)


_vc_texts = []
_vc_cfg = _types.SimpleNamespace(VC_DEVICE_NAME="Voice Chat", VC_MIN_CHARS=4,
                                 VC_ENERGY_THRESHOLD=0.01, VC_END_SILENCE_SECONDS=0.3)
_vc = vcl.VCListener(_vc_cfg, transcribe=lambda a: "太郎、今の見た？すごくない？",
                     on_text=_vc_texts.append,
                     device_finder=lambda n: _FakeDev([_loud] * 6 + [_sil] * 4))
check("VC: 聞き始められる", _vc.set_listening(True) is True and _vc.listening)
for _ in range(50):
    if _vc_texts:
        break
    time.sleep(0.05)
check("VC: 横取りした声を文字にして渡す", _vc_texts[:1] == ["太郎、今の見た？すごくない？"])
check("VC: 止められる", _vc.set_listening(False) is True and not _vc.listening)
_vc_none = vcl.VCListener(_vc_cfg, transcribe=lambda a: "", on_text=lambda t: None,
                          device_finder=lambda n: None)
check("VC: 出力先が見つからなければ聞き始めない", _vc_none.set_listening(True) is False and not _vc_none.listening)

check("VC: 「チョチョチョ…」のような繰り返しは聞き間違いとして捨てる",
      vcl.looks_like_hallucination("チョ" * 30))
check("VC: 「ナイス、ナイス、ナイス」程度の普通の繰り返しは捨てない",
      not vcl.looks_like_hallucination("ナイス、ナイス、ナイス。どこで返せる?"))
check("VC: 普通の発言は捨てない", not vcl.looks_like_hallucination("一旦あおちゃんは起こしてもらえるかも。"))

# 頭脳（lane_manager）側
cfg_vc = FakeConfig()
gen_vc = CommentGenerator(cfg_vc)
_vc_prompts = []
gen_vc._call_gemini = lambda p, **kw: _vc_prompts.append(p) or "え、俺のこと呼んだ？見てたよ！"
tw_vc = FakeTwitch()
lanes_vc = LaneManager(cfg_vc, gen_vc, tw_vc, FakeAudio())
check("VC命令: 「VC聞いて」→聞く", lanes_vc._detect_vc_command("VC聞いて") is True)
check("VC命令: 「ボイチャはいいよ」→聞かない", lanes_vc._detect_vc_command("ボイチャはいいよ") is False)
check("VC命令: 「vc聞かなくていいよ」→聞かない", lanes_vc._detect_vc_command("vc聞かなくていいよ") is False)
check("VC命令: VCと言っていなければ命令ではない", lanes_vc._detect_vc_command("これ聞いて") is None)
check("VC声命令: 「VCでしゃべって」→流す", lanes_vc._detect_vc_talk_command("太郎、VCでしゃべって") is True)
check("VC声命令: 「VCでは黙って」→流さない", lanes_vc._detect_vc_talk_command("太郎、VCでは黙って") is False)
check("VC声命令: 「VCでしゃべるのやめて」→流さない", lanes_vc._detect_vc_talk_command("VCでしゃべるのやめて") is False)
check("VC声命令: 「ボイチャに声出して」→流す", lanes_vc._detect_vc_talk_command("ボイチャに声出して") is True)
check("VC声命令: 「VC聞いて」は聞く側の命令", lanes_vc._detect_vc_talk_command("VC聞いて") is None)

lanes_vc.on_vc_speech("ナイス、今の撃ち合い勝ったね")
check("VC: 呼ばれていなければ返事しない（割り込まない）", tw_vc.sent == [] and _vc_prompts == [])
check("VC: 呼ばれていない発言も一時メモには残す", list(lanes_vc._vc_memo) == ["ナイス、今の撃ち合い勝ったね"])
lanes_vc.on_vc_speech("太郎って見てるの？")
check("VC: 太郎と呼ばれたら返事する", tw_vc.sent == [("え、俺のこと呼んだ？見てたよ！", True)])
check("VC: 返事には直前のVCの会話が添えられる", "撃ち合い勝った" in _vc_prompts[0])
check("VC: 仲間の発言は手帳（会話履歴）に入れない",
      not any("撃ち合い" in str(m) for m in gen_vc._conversation_history))
lanes_vc.on_vc_speech("じゃあ次どこ行く？")
check("VC会話モード: 返事の直後は名前なしでも返事する", len(tw_vc.sent) == 2)
check("VC会話モード: 続きの発言をGeminiに渡す", "じゃあ次どこ行く" in _vc_prompts[-1])
lanes_vc.on_vc_speech("そっちの建物にしよう")
check("VC会話モード: 3往復目まで返事する", len(tw_vc.sent) == 3)
lanes_vc.on_vc_speech("オッケー、行こう")
check("VC会話モード: 3往復で一区切り（4回目は返さない）", len(tw_vc.sent) == 3)
lanes_vc.on_vc_speech("太郎、聞こえてる？")
check("VC: 一区切りの直後に呼ばれても、20秒以内なら連続では返さない", len(tw_vc.sent) == 3)
lanes_vc._last_vc_reply -= 30
lanes_vc.on_vc_speech("タロウ、聞こえてる？")
check("VC: 「タロウ」と書かれても呼ばれたと分かる（20秒たった後）", len(tw_vc.sent) == 4)
lanes_vc._vc_conv_until = 0.0
lanes_vc.on_vc_speech("関係ない仲間どうしの話")
check("VC: 会話モードが切れたら、呼ばれない発言には返事しない", len(tw_vc.sent) == 4)

# v4.59 合図（「返事して」「返事してくれない」等）と、さかのぼり
lanes_vc._last_vc_reply -= 30
lanes_vc.on_vc_speech("ねえ返事してくれないじゃん")
check("VC合図: 仲間の「返事してくれない」で返事する（名前なしでも）", len(tw_vc.sent) == 5)
_lb_calls = []
lanes_vc.set_vc_lookback(lambda: _lb_calls.append(1) or "さっき太郎って呼んだんだけど聞こえた？", lambda: True)
lanes_vc._last_vc_reply -= 30
lanes_vc._vc_conv_until = 0.0
lanes_vc.on_speech("VCに返事して")
check("VC合図: マイクの「VCに返事して」で返事する（太郎と呼ばなくても）", len(tw_vc.sent) == 6)
check("VC合図: 直近のVCをさかのぼって聞き直し、それを返事の材料にする",
      _lb_calls == [1] and "さっき太郎って呼んだ" in _vc_prompts[-1])
check("VC合図: マイクからの合図だと分かる形で渡す", "配信者があなたに" in _vc_prompts[-1])
lanes_vc._last_vc_reply -= 30
lanes_vc._vc_conv_until = 0.0
lanes_vc.on_speech("太郎、今のVC聞いてた？")
check("VC合図: 「太郎、今のVC聞いてた？」も合図（聞くのオン命令と取り違えない）", len(tw_vc.sent) == 7)
check("マイク合図: VCを聞いていないときは合図扱いしない",
      LaneManager(cfg_vc, gen_vc, FakeTwitch(), FakeAudio()).is_mic_vc_nudge("返事してくれない") is False)
_vr = vcl.VCListener(_vc_cfg, transcribe=lambda a: "", on_text=lambda t: None, device_finder=lambda n: None)
check("VC: 録っていなければ、さかのぼる音は無い", _vr.get_recent_audio() is None)
_vr._recent.extend([_loud, _sil])
check("VC: 直近の音をつなげて返す", len(_vr.get_recent_audio()) == 3200)

_toggles = []
lanes_vc.set_vc_toggle(lambda on: _toggles.append(on) or True)
lanes_vc.on_speech(f"{cfg_vc.AI_NAME}、VC聞いて")
check("VC命令: 声で「太郎、VC聞いて」→切り替えが呼ばれる", _toggles == [True])
check("VC命令: 切り替えたらチャットで了解と返す", tw_vc.sent[-1][0].startswith("了解、VCも聞いとくね"))

# v4.59 記録をファイルにも残す
_lg_dir = '/tmp/taro_test_logs'
shutil.rmtree(_lg_dir, ignore_errors=True)
os.makedirs(os.path.join(_lg_dir, "logs"), exist_ok=True)
_old_log = os.path.join(_lg_dir, "logs", "taro_2000-01-01.log")
open(_old_log, "w").close()
os.utime(_old_log, (time.time() - 40 * 86400, time.time() - 40 * 86400))
_fh = _gui.make_file_log_handler(_lg_dir)
_tl = _logging.getLogger("test_file_log")
_tl.addHandler(_fh); _tl.setLevel(_logging.INFO)
_tl.info("読み上げのテスト記録です")
_fh.flush(); _tl.removeHandler(_fh); _fh.close()
_today_logs = [f for f in os.listdir(os.path.join(_lg_dir, "logs")) if f != "taro_2000-01-01.log"]
check("記録ファイル: 今日の日付のファイルに書かれる",
      len(_today_logs) == 1 and "読み上げのテスト記録です" in open(
          os.path.join(_lg_dir, "logs", _today_logs[0]), encoding="utf-8").read())
check("記録ファイル: 30日より古いものは消える", not os.path.exists(_old_log))
check("記録ファイル: 画面の記録の整え直しでも残る目印が付いている", getattr(_fh, "_taro_file_log", False))

# 起動ボタンの種類 → 設定値
_m = _types.SimpleNamespace(TARO_AI_ENABLED=True, READ_ALOUD_ENABLED=False)
_gui.BotGUI._apply_run_mode(_m, "hybrid")
check("▶太郎＋読み上げ: 太郎も読み上げも動く", _m.TARO_AI_ENABLED and _m.READ_ALOUD_ENABLED)
_gui.BotGUI._apply_run_mode(_m, "read_only")
check("▶読み上げだけ: 太郎は動かず読み上げだけ", (not _m.TARO_AI_ENABLED) and _m.READ_ALOUD_ENABLED)
_gui.BotGUI._apply_run_mode(_m, "taro_only")
check("▶太郎だけ: 今までどおり（読み上げはTTA任せ）", _m.TARO_AI_ENABLED and not _m.READ_ALOUD_ENABLED)

print()
# ============================================================
# v4.59 「黙れ」でしばらく黙る
# ============================================================
cfg_q = FakeConfig()
cfg_q.QUIET_WORDS = "黙れ,だまれ"
cfg_q.QUIET_SECONDS = 180
gen_q = CommentGenerator(cfg_q)
gen_q._call_gemini = lambda p, **kw: "はいよ！"
tw_q = FakeTwitch()
lanes_q = LaneManager(cfg_q, gen_q, tw_q, FakeAudio())
check("黙れ: ふだんは黙っていない", lanes_q.is_quiet() is False)
lanes_q.on_speech("黙れ黙れ黙れ")
check("黙れ: 言われたら黙る（返事もしない）", lanes_q.is_quiet() is True and tw_q.sent == [])
lanes_q._send_normal("文脈のコメント")
lanes_q._send_priority("名指しへの返事")
check("黙れ: 黙っている間は投稿しない", tw_q.sent == [])
lanes_q.on_vc_speech("太郎、返事して")
lanes_q.on_speech("今日は調子がいいですね、どんどん行きましょう")
check("黙れ: 黙っている間はVCの呼びかけにも普通の発言にも反応しない", tw_q.sent == [])
lanes_q.on_speech("太郎、もう喋っていいよ")
check("黙れ: 「太郎」と呼べばすぐ戻って返事する", lanes_q.is_quiet() is False and len(tw_q.sent) == 1)
lanes_q.on_speech("だまれ")
lanes_q._quiet_until = time.time() - 1
check("黙れ: 時間が過ぎたら元に戻る", lanes_q.is_quiet() is False)
lanes_q._send_normal("戻ったあとのコメント")
check("黙れ: 戻ったら投稿できる", tw_q.sent[-1][0] == "戻ったあとのコメント")

import audio_module as _am_q
_emitted_q = []
_a_q = _am_q.AudioModule.__new__(_am_q.AudioModule)
_a_q.config = cfg_q
check("黙れ: 2文字でも聞き取りのフィルターを通す", _a_q._has_quiet_word("黙れ") and not _a_q._has_quiet_word("よし"))

# v4.59 VC: ヒント文がそのまま出てきたものと、決まり文句を捨てる
_vp = "ゲームのボイスチャットの会話。太郎、返事して、返事してくれない、無視、フォートナイト、ナイス、などの言葉が出ます。"
check("VC: ヒント文の先頭がそのまま出たら捨てる", vcl.is_prompt_echo("ゲームのボイスチャットの会話。", _vp))
check("VC: ヒント文のかけらをつないだだけなら捨てる", vcl.is_prompt_echo("無視、フォートナイト", _vp))
check("VC: 本当の発言は捨てない（返事して）", not vcl.is_prompt_echo("ねえ、太郎返事してくれないじゃん", _vp))
check("VC: 本当の発言は捨てない（ナイス）", not vcl.is_prompt_echo("ナイス。", _vp))
check("VC: 本当の発言は捨てない（太郎）", not vcl.is_prompt_echo("太郎、こんばんは。", _vp))
check("VC: 「ご視聴ありがとうございました」は捨てる", vcl.is_stock_hallucination("ご視聴ありがとうございました。"))
check("VC: 似ていても普通の発言は捨てない", not vcl.is_stock_hallucination("ご視聴ありがとうございましたって言われたよ"))

ok = sum(1 for _, c in results if c)
print(f"===== 結果: {ok}/{len(results)} 件成功 =====")
sys.exit(0 if ok == len(results) else 1)
