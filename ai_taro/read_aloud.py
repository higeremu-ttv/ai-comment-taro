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
- 太郎の声（§3）も将来この同じ列に並べる（enqueue_wav）。読み上げ同士が重ならない。
"""

import logging
import os
import queue
import subprocess
import threading
import time

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


class ReadAloudWorker:

    def __init__(self, config, gemini_api_key: str = "", base_dir: str = "",
                 play_func=_play_wav, pipeline_func=reading_pipeline.read_comment,
                 engine_check=voicevox_client.is_engine_running):
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
        self.max_chars_ja = getattr(config, "READ_ALOUD_MAX_CHARS_JA", 30)
        self.english_voice = getattr(config, "READ_ALOUD_ENGLISH_VOICE", "Puck")

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
        """合成済みの音声を列に並べる（太郎の声 §3 用）"""
        if wav:
            self._queue.put(("wav", wav))

    # ------------------------------------------------------------
    # 本体（専用スレッド）
    # ------------------------------------------------------------
    def _run(self):
        while True:
            item = self._queue.get()
            if item is _STOP:
                break
            try:
                if item[0] == "wav":
                    wav = item[1]
                else:
                    _, username, text, emote_names = item
                    wav = self._pipeline(
                        username, text, gemini_api_key=self.gemini_api_key,
                        dictionary_data=self.dictionary, exclusions_data=self.exclusions,
                        gemini_synthesize=self._gemini_english,
                        emote_names=emote_names, max_chars_ja=self.max_chars_ja)
                if wav:
                    self._play(wav)
            except Exception as e:
                logger.warning(f"読み上げに失敗: {e}")
