"""
音声認識モジュール v3.1

【残した機能】
- マイク音声キャプチャ・Google Web Speech APIでテキスト変換
- 最小文字数フィルター（短すぎる発言を無視）
- 途中切れ発言の結合待機
- 無言検知

【削除したもの】
- VC向け指示語フィルター
- 聞き取り失敗コールバック
"""

import threading
import time
import logging
import re
from collections import deque
from typing import Callable, Optional

logger = logging.getLogger(__name__)

INCOMPLETE_ENDINGS = re.compile(
    r'(は|が|を|に|で|と|も|の|へ|から|まで|より|や|か|て|し|けど|けれど|だけど|だし|だから|なので|ので|って|とか|など|とは|では)$'
)


class AudioModule:

    def __init__(self, config):
        self.config = config
        self.is_running = False
        self.speech_callback: Optional[Callable[[str], None]] = None
        self.silence_callback: Optional[Callable[[], None]] = None
        self.unrecognized_callback: Optional[Callable] = None  # 後方互換用（未使用）

        self._last_speech_time = time.time()
        self._silence_notified = False
        self._speech_context = deque(maxlen=20)
        self._min_length = getattr(config, 'SPEECH_MIN_LENGTH', 4)

        # 途中切れ発言の結合設定
        self._merge_enabled = getattr(config, 'INCOMPLETE_SPEECH_MERGE_ENABLED', True)
        self._merge_wait = getattr(config, 'INCOMPLETE_SPEECH_WAIT_SECONDS', 8)
        self._pending_fragment: Optional[str] = None
        self._fragment_timer: Optional[threading.Timer] = None
        self._fragment_lock = threading.Lock()

    def set_speech_callback(self, callback: Callable[[str], None]):
        self.speech_callback = callback

    def set_silence_callback(self, callback: Callable[[], None]):
        self.silence_callback = callback

    def set_unrecognized_callback(self, callback: Callable):
        self.unrecognized_callback = callback  # 受け取るが使わない

    def get_speech_context(self) -> str:
        context = " ".join(self._speech_context)
        return context[-getattr(self.config, 'MAX_SPEECH_CONTEXT_CHARS', 500):]

    def get_seconds_since_last_speech(self) -> float:
        return time.time() - self._last_speech_time

    def _is_incomplete_speech(self, text: str) -> bool:
        if not self._merge_enabled:
            return False
        ai_name = getattr(self.config, 'AI_NAME', '')
        if ai_name and ai_name in text:
            return False
        stripped = text.strip()
        if re.search(r'[。！？!?]$', stripped):
            return False
        if INCOMPLETE_ENDINGS.search(stripped):
            return True
        if len(stripped) <= 8:
            return True
        return False

    def _is_valid_speech(self, text: str) -> tuple:
        """
        Geminiに渡す前にローカルで品質チェックする。
        Returns: (is_valid: bool, reason: str)
        """
        # 日本語文字の比率チェック
        hiragana = sum(1 for c in text if '\u3040' <= c <= '\u309F')
        katakana = sum(1 for c in text if '\u30A0' <= c <= '\u30FF')
        kanji = sum(1 for c in text if '\u4E00' <= c <= '\u9FFF')
        total = len(text.replace(' ', ''))
        if total > 0:
            japanese_ratio = (hiragana + katakana + kanji) / total
            if japanese_ratio < 0.3:
                return False, f"日本語比率が低すぎます（{japanese_ratio:.0%}）"

        # 同じ文字の繰り返しパターン検知
        import re as _re
        if _re.search(r'(.)\1{4,}', text):
            return False, "同じ文字の繰り返しを検出"

        # NGワードチェック（Gemini前に弾く）
        ng_words_str = getattr(self._config_ref, 'NG_WORDS', '') if hasattr(self, '_config_ref') else ''
        if ng_words_str:
            ng_words = [w.strip() for w in ng_words_str.split(',') if w.strip()]
            for word in ng_words:
                if word in text:
                    return False, f"NGワード検出: '{word}'"

        return True, ""

    def _emit_speech(self, text: str):
        logger.info(f"音声認識結果: {text}")
        self._speech_context.append(text)
        if self.speech_callback:
            self.speech_callback(text)

    def _handle_fragment_timeout(self):
        with self._fragment_lock:
            if self._pending_fragment:
                text = self._pending_fragment
                self._pending_fragment = None
                logger.info(f"[途中切れ] 待機タイムアウト、そのまま送信: '{text}'")
                self._emit_speech(text)

    def _process_speech_text(self, text: str):
        with self._fragment_lock:
            if self._pending_fragment is not None:
                combined = self._pending_fragment + text
                logger.info(f"[途中切れ結合] '{self._pending_fragment}' + '{text}' → '{combined}'")
                self._pending_fragment = None
                if self._fragment_timer:
                    self._fragment_timer.cancel()
                    self._fragment_timer = None
                text = combined

            if self._is_incomplete_speech(text):
                self._pending_fragment = text
                if self._fragment_timer:
                    self._fragment_timer.cancel()
                self._fragment_timer = threading.Timer(
                    self._merge_wait, self._handle_fragment_timeout
                )
                self._fragment_timer.daemon = True
                self._fragment_timer.start()
                logger.info(f"[途中切れ待機] '{text}' → {self._merge_wait}秒待機中")
                return

        # Geminiに渡す前の品質チェック
        is_valid, reason = self._is_valid_speech(text)
        if not is_valid:
            logger.info(f"[前段階フィルター] スキップ: '{text}' - {reason}")
            return

        self._emit_speech(text)

    def set_config(self, config):
        """configの参照をセット（NGワードチェック用）"""
        self._config_ref = config

    def load_model(self):
        try:
            import speech_recognition as sr  # noqa
            logger.info("SpeechRecognition (Google Web Speech API) を使用します")
        except ImportError:
            logger.error("SpeechRecognitionライブラリが見つかりません。pip install SpeechRecognition")
            raise

    def _find_microphone(self, sr):
        mic_list = sr.Microphone.list_microphone_names()
        logger.info(f"検出されたマイクデバイス数: {len(mic_list)}")
        for i, name in enumerate(mic_list):
            try:
                safe_name = name.encode('utf-8', errors='replace').decode('utf-8')
            except Exception:
                safe_name = repr(name)
            logger.info(f"  [{i}] {safe_name}")
        if hasattr(self.config, 'MICROPHONE_INDEX') and self.config.MICROPHONE_INDEX is not None:
            idx = self.config.MICROPHONE_INDEX
            logger.info(f"指定されたマイクを使用: [{idx}]")
            return idx
        logger.info("デフォルトマイクを使用します")
        return None

    def _audio_capture_loop(self):
        try:
            import speech_recognition as sr
        except ImportError:
            logger.error("SpeechRecognitionライブラリが見つかりません。")
            return

        recognizer = sr.Recognizer()
        recognizer.energy_threshold = getattr(self.config, 'MICROPHONE_ENERGY_THRESHOLD', 300)
        recognizer.dynamic_energy_threshold = True
        recognizer.pause_threshold = getattr(self.config, 'SPEECH_PAUSE_THRESHOLD', 2.0)
        recognizer.phrase_threshold = 0.3
        recognizer.non_speaking_duration = 0.8

        mic_index = self._find_microphone(sr)

        try:
            mic = sr.Microphone(device_index=mic_index, sample_rate=16000)
        except Exception as e:
            logger.error(f"マイクの初期化に失敗しました: {e}")
            return

        logger.info("マイクのキャリブレーション中（約2秒）...")
        try:
            with mic as source:
                recognizer.adjust_for_ambient_noise(source, duration=2)
            logger.info(f"キャリブレーション完了 (energy_threshold={recognizer.energy_threshold:.0f})")
        except Exception as e:
            logger.warning(f"キャリブレーション失敗（続行します）: {e}")

        merge_status = "有効" if self._merge_enabled else "無効"
        logger.info(f"音声キャプチャ開始（最小文字数:{self._min_length}文字 / 途中切れ結合:{merge_status}）")

        silence_thread = threading.Thread(target=self._silence_check_loop, daemon=True)
        silence_thread.start()

        while self.is_running:
            try:
                with mic as source:
                    try:
                        audio = recognizer.listen(source, timeout=5, phrase_time_limit=60)
                    except sr.WaitTimeoutError:
                        continue

                self._last_speech_time = time.time()
                self._silence_notified = False

                try:
                    text = recognizer.recognize_google(audio, language="ja-JP")
                    if text:
                        text = text.strip()
                        if len(text) < self._min_length:
                            logger.info(f"[フィルター] スキップ: '{text}' ({len(text)}文字)")
                        else:
                            self._process_speech_text(text)

                except sr.UnknownValueError:
                    logger.debug("音声認識できませんでした（無音または雑音）")
                except sr.RequestError as e:
                    logger.error(f"Google Speech APIエラー: {e}")
                    time.sleep(5)

            except Exception as e:
                if self.is_running:
                    logger.error(f"音声キャプチャエラー: {e}")
                    time.sleep(2)

        logger.info("音声キャプチャを終了しました")

    def _silence_check_loop(self):
        while self.is_running:
            silence_duration = self.get_seconds_since_last_speech()
            if (silence_duration >= self.config.SILENCE_COMMENT_THRESHOLD
                    and not self._silence_notified
                    and self.silence_callback):
                logger.info(f"無言状態が{silence_duration:.0f}秒続いています")
                self._silence_notified = True
                self.silence_callback()
            time.sleep(5)

    def start(self):
        self.load_model()
        self.is_running = True
        self._thread = threading.Thread(target=self._audio_capture_loop, daemon=True)
        self._thread.start()
        logger.info("音声認識モジュールを起動しました（Google Web Speech API）")

    def stop(self):
        self.is_running = False
        with self._fragment_lock:
            if self._fragment_timer:
                self._fragment_timer.cancel()
                self._fragment_timer = None
            self._pending_fragment = None
        if hasattr(self, '_thread'):
            self._thread.join(timeout=5)
        logger.info("音声認識モジュールを停止しました")
