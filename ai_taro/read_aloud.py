"""
読み上げ係 v4.57（§2 改造A の配線部）

Twitchのコメントを受け取るたびに順番待ちの列（キュー）へ入れ、専用のスレッドで
1件ずつ合成・再生する。Twitchの受信処理を止めないため、合成（ネットワーク越し）と
再生（数秒かかる）はすべてこのスレッドの中で行う。

- VOICEVOXエンジン（画面なし）が動いていなければ太郎が起動し、太郎の終了時に止める。
  最初から動いていたエンジン（おじさんがVOICEVOXを手で開いていた等）は止めない。
- 再生はWindows標準の既定の出力デバイスへ。配信には、OBSの「アプリケーション音声キャプチャ」で
  太郎のプロセス（pythonw.exe、実行ファイル名で一致）を拾って乗せる。デスクトップ音声は配信に
  乗らない設定のため、このソースが無いと読み上げは配信で聞こえない（棒読みちゃんも同じ方式で
  BouyomiChan.exe を拾っていた。2026-09-29 OBS設定ファイルで確認）。
- 太郎の発言（§3）: TARO_VOICE_ENABLED がオンなら Gemini の声（Algieba・生意気な口調・日本語固定）で読む。
  口調は本文とは別枠で渡す（v4.58。本文に書くと指示文まで読み上げたため）。
  声づくりに5〜7秒かかるため、投稿した瞬間に別の手で作り始め（並行）、再生だけ列の順番を守る。
  その間も視聴者コメントの読み上げは止まらない。Geminiが失敗したらVOICEVOXで読む（黙らない）。
  オフなら視聴者コメントと同じくVOICEVOXで読む（今までのTwitchTalkAppと同じ）。
"""

import logging
import os
import queue
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import gemini_tts_client
import reading_dictionary
import reading_exclusions
import reading_pipeline
import voicevox_client

logger = logging.getLogger(__name__)

_STOP = object()


def _play_wav(wav: bytes):
    import winsound
    winsound.PlaySound(wav, winsound.SND_MEMORY)


def normalize_wav(wav: bytes, target_dbfs: float = -21.0, peak_dbfs: float = -3.0) -> bytes:
    """v4.58: 声の大きさをそろえる。
    VOICEVOXは平均 約-21.5dBFSで安定しているが、Geminiは -13〜-19dBFS とセリフごとにばらつき、
    しかも大きい（2026-09-30 実測）。太郎の音はOBSで1本のソースにまとまるため、OBS側では
    エンジンごとに調整できない。鳴らす直前に、声の部分の平均の大きさを target_dbfs にそろえる。
    大きくしすぎて割れないよう、一番大きいところは peak_dbfs までに抑える。
    16bitのWAV以外・読めないデータはそのまま返す（黙るよりまし）"""
    try:
        import io
        import wave
        import numpy as np
        with wave.open(io.BytesIO(wav)) as w:
            params = w.getparams()
            if params.sampwidth != 2:
                return wav
            frames = w.readframes(params.nframes)
        x = np.frombuffer(frames, dtype=np.int16).astype(np.float64) / 32768.0
        voiced = x[np.abs(x) > 0.01]  # 無音部分は平均に入れない
        if len(voiced) == 0:
            return wav
        rms = float(np.sqrt(np.mean(voiced ** 2)))
        gain = (10 ** (target_dbfs / 20)) / rms
        peak = float(np.max(np.abs(x)))
        if peak * gain > 10 ** (peak_dbfs / 20):
            gain = (10 ** (peak_dbfs / 20)) / peak
        y = np.clip(x * gain, -1.0, 1.0)
        out = io.BytesIO()
        with wave.open(out, "wb") as w:
            w.setparams(params)
            w.writeframes((y * 32767).astype(np.int16).tobytes())
        return out.getvalue()
    except Exception as e:
        logger.debug(f"音量そろえをスキップ: {e}")
        return wav


class ReadAloudWorker:

    def __init__(self, config, gemini_api_key: str = "", base_dir: str = "",
                 play_func=_play_wav, pipeline_func=reading_pipeline.read_comment,
                 engine_check=voicevox_client.is_engine_running,
                 taro_synth=gemini_tts_client.speak_as_taro):
        self.config = config
        self.gemini_api_key = gemini_api_key
        self.base_dir = base_dir or os.path.dirname(os.path.abspath(__file__))
        self._play = play_func
        self._pipeline = pipeline_func
        self._engine_check = engine_check
        self._queue = queue.Queue()
        self._thread = None
        self._engine_proc = None
        self.dictionary = reading_dictionary.load_dictionary(self.base_dir)
        self.exclusions = reading_exclusions.load_exclusions(self.base_dir)
        self.max_queue = getattr(config, "READ_ALOUD_MAX_QUEUE", 50)
        self.max_chars_ja = getattr(config, "READ_ALOUD_MAX_CHARS_JA", 150)
        self.english_voice = getattr(config, "READ_ALOUD_ENGLISH_VOICE", "Puck")
        self.normalize_enabled = getattr(config, "READ_ALOUD_NORMALIZE", True)
        self.target_dbfs = getattr(config, "READ_ALOUD_TARGET_DBFS", -21.0)
        self._taro_synth = taro_synth
        self.taro_voice_enabled = getattr(config, "TARO_VOICE_ENABLED", False)
        self.taro_voice_name = getattr(config, "TARO_VOICE_NAME", "Algieba")
        self.taro_voice_model = getattr(config, "TARO_VOICE_MODEL", "gemini-3.8-flash-tts")
        self.taro_voice_fallback_model = getattr(config, "TARO_VOICE_FALLBACK_MODEL", "gemini-3.8-flash-lite-tts")
        self.taro_voice_style = getattr(config, "TARO_VOICE_STYLE", "")
        self.taro_voice_language = getattr(config, "TARO_VOICE_LANGUAGE", "ja-JP")
        self.taro_voice_replace = getattr(config, "TARO_VOICE_REPLACE", "")
        # 読み方の指示は口調の後ろに付ける（どちらも本文とは別枠なので声には出ない）
        pron = getattr(config, "TARO_VOICE_PRONUNCIATION", "")
        if pron:
            self.taro_voice_style = (self.taro_voice_style + "。" + pron) if self.taro_voice_style else pron
        self.bot_nick = getattr(config, "BOT_NICK", "")
        # 太郎の声づくり専用の手（再生の列とは別に、先に作り始めておくため）
        self._taro_pool = ThreadPoolExecutor(max_workers=2)

    def _gemini_english(self, text, api_key):
        return gemini_tts_client.synthesize(text, api_key=api_key, voice_name=self.english_voice)

    # ------------------------------------------------------------
    # 起動・終了
    # ------------------------------------------------------------
    def start(self):
        self._ensure_engine()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("読み上げ係を起動しました")

    def stop(self):
        """止める。×ボタンからも呼ばれるので、画面が長く固まらないよう待ちは3秒まで。
        2回呼ばれても安全（×ボタン時とボットスレッド終了時の両方から呼ばれうる）"""
        if getattr(self, "_stopped", False):
            return
        self._stopped = True
        self._queue.put(_STOP)
        if self._thread:
            self._thread.join(timeout=3)
        self._taro_pool.shutdown(wait=False)
        self._stop_engine()
        logger.info("読み上げ係を停止しました")

    def _ensure_engine(self):
        if self._engine_check():
            logger.info("VOICEVOXエンジンは既に起動しています（太郎は止めません）")
            return
        path = getattr(self.config, "VOICEVOX_ENGINE_PATH", "")
        if not path or not os.path.exists(path):
            logger.warning(f"VOICEVOXエンジンが見つかりません: {path}。日本語の読み上げはできません")
            return
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self._engine_proc = subprocess.Popen(
                [path, "--host", "127.0.0.1", "--port", "50021"],
                creationflags=flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            logger.warning(f"VOICEVOXエンジンを起動できません: {e}")
            return
        for _ in range(60):  # 最大30秒待つ
            if self._engine_check():
                logger.info("VOICEVOXエンジンを起動しました")
                return
            time.sleep(0.5)
        logger.warning("VOICEVOXエンジンの起動待ちがタイムアウトしました")

    def _stop_engine(self):
        if self._engine_proc is None:
            return
        try:
            self._engine_proc.terminate()
            self._engine_proc.wait(timeout=10)
        except Exception as e:
            logger.warning(f"VOICEVOXエンジンの停止に失敗: {e}")
        self._engine_proc = None

    # ------------------------------------------------------------
    # 受け付け（Twitchの受信スレッドから呼ばれる。すぐ戻る）
    # ------------------------------------------------------------
    def enqueue_comment(self, username: str, text: str, emote_names=None):
        if self._queue.qsize() >= self.max_queue:
            logger.warning("読み上げの列が詰まっているため、このコメントは読みません")
            return
        self._queue.put(("comment", username, text, emote_names))

    def enqueue_wav(self, wav: bytes):
        """合成済みの音声を列に並べる"""
        if wav:
            self._queue.put(("wav", wav))

    def enqueue_done_marker(self, event):
        """ここまで並んだ分を流し終えたら event.set() する（読み上げテスト用）"""
        self._queue.put(("done", event))

    def enqueue_taro(self, text: str):
        """太郎の発言を読む（§3）。Geminiの声がオンなら、ここで声づくりを始めてから列に並べる"""
        if not text:
            return
        if not self.taro_voice_enabled:
            self.enqueue_comment(self.bot_nick, text)
            return
        future = self._taro_pool.submit(
            self._taro_synth, text, api_key=self.gemini_api_key,
            style=self.taro_voice_style, model=self.taro_voice_model,
            voice_name=self.taro_voice_name, language_code=self.taro_voice_language,
            replacements=self.taro_voice_replace)
        self._queue.put(("taro", future, text))

    # ------------------------------------------------------------
    # 本体（専用スレッド）
    # ------------------------------------------------------------
    def _run(self):
        while True:
            item = self._queue.get()
            if item is _STOP:
                break
            try:
                if item[0] == "done":
                    item[1].set()
                    continue
                if item[0] == "wav":
                    wav = item[1]
                elif item[0] == "taro":
                    _, future, text = item
                    try:
                        wav = future.result(timeout=30)
                    except Exception as e:
                        logger.warning(f"太郎の声（Gemini）の作成に失敗: {e}")
                        wav = None
                    if not wav and self.taro_voice_fallback_model \
                            and self.taro_voice_fallback_model != self.taro_voice_model:
                        # v4.59: 上位版が上限（1分10回）等で断ったら、上限が別枠の軽量版で同じ声を作り直す
                        logger.info("太郎の声: 軽量版モデルで作り直します")
                        wav = self._taro_synth(
                            text, api_key=self.gemini_api_key, style=self.taro_voice_style,
                            model=self.taro_voice_fallback_model, voice_name=self.taro_voice_name,
                            language_code=self.taro_voice_language, replacements=self.taro_voice_replace)
                    if not wav:
                        logger.warning("太郎の声が作れなかったため、VOICEVOXで読みます")
                        wav = self._pipeline(
                            self.bot_nick, text, gemini_api_key=self.gemini_api_key,
                            dictionary_data=self.dictionary, exclusions_data=self.exclusions,
                            gemini_synthesize=self._gemini_english,
                            emote_names=None, max_chars_ja=self.max_chars_ja)
                else:
                    _, username, text, emote_names = item
                    wav = self._pipeline(
                        username, text, gemini_api_key=self.gemini_api_key,
                        dictionary_data=self.dictionary, exclusions_data=self.exclusions,
                        gemini_synthesize=self._gemini_english,
                        emote_names=emote_names, max_chars_ja=self.max_chars_ja)
                if wav:
                    if self.normalize_enabled:
                        wav = normalize_wav(wav, self.target_dbfs)
                    self._play(wav)
            except Exception as e:
                logger.warning(f"読み上げに失敗: {e}")
