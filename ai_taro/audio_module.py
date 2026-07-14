"""
音声認識モジュール v4.0

【機能】
- マイク音声キャプチャ（speech_recognitionのエネルギーベースVAD）
- 認識エンジン切り替え対応:
    - "whisper": faster-whisper（ローカル・GPU/CPU・課金なし・高精度）※推奨
    - "google" : Google Web Speech API（従来方式・無料・低精度）
- 最小文字数フィルター（短すぎる発言を無視）
- 途中切れ発言の結合待機
- 無言検知
- Whisperハルシネーション対策（定番の幻聴フレーズを破棄）
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
        self._whisper_error_count = 0
        self._extra_vocab = []  # v4.20: 手帳の固有名詞辞書から流れてくる追加語彙
        self._corrections = {}  # v4.43: 訂正辞書（誤変換された語→正しい語）

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

    def set_extra_vocabulary(self, terms: list):
        """手帳の固有名詞辞書をWhisperの認識ヒントに追加する（v4.20）"""
        self._extra_vocab = [t for t in (terms or []) if t][:20]
        if self._extra_vocab:
            logger.info(f"認識ヒントに手帳の語彙を追加: {len(self._extra_vocab)}語")

    def set_corrections(self, corrections: dict):
        """訂正辞書をセットする（v4.43）。認識結果の誤変換を即座に直す"""
        self._corrections = {
            k: v for k, v in (corrections or {}).items()
            if k and v and k != v and len(k) >= 2
        }
        if self._corrections:
            logger.info(f"訂正辞書を適用: {len(self._corrections)}件（誤変換を自動補正します）")

    def _apply_corrections(self, text: str) -> str:
        """認識結果に訂正辞書を適用する（v4.43）"""
        for wrong, right in self._corrections.items():
            if wrong in text:
                text = text.replace(wrong, right)
                logger.info(f"[誤変換補正] 「{wrong}」→「{right}」")
        return text

    def load_model(self):
        """認識エンジンを初期化する。whisper指定で失敗した場合はgoogleにフォールバック"""
        try:
            import speech_recognition as sr  # noqa
        except ImportError:
            logger.error("SpeechRecognitionライブラリが見つかりません。pip install SpeechRecognition")
            raise

        self._engine = getattr(self.config, 'SPEECH_ENGINE', 'whisper').lower()
        self._whisper_model = None

        if self._engine == 'whisper':
            if not self._load_whisper():
                logger.warning("Whisperの初期化に失敗したため、Google Web Speech APIにフォールバックします")
                self._engine = 'google'

        if self._engine == 'google':
            logger.info("認識エンジン: Google Web Speech API（クラウド・無料枠）")

    def _register_cuda_dlls(self):
        """Windows: pipでインストールされたCUDA関連DLLの場所をOSに登録する。
        （nvidia-cublas-cu12等はsite-packages内の深い場所にDLLを置くため、
        　そのままではctranslate2から見つけられない）"""
        import os
        import sys
        if sys.platform != 'win32':
            return
        try:
            import site
            candidates = []
            for sp in site.getsitepackages() + [site.getusersitepackages()]:
                nvidia_dir = os.path.join(sp, 'nvidia')
                if os.path.isdir(nvidia_dir):
                    for pkg in os.listdir(nvidia_dir):
                        bin_dir = os.path.join(nvidia_dir, pkg, 'bin')
                        if os.path.isdir(bin_dir):
                            candidates.append(bin_dir)
            for d in candidates:
                try:
                    os.add_dll_directory(d)
                    os.environ['PATH'] = d + os.pathsep + os.environ.get('PATH', '')
                except Exception:
                    pass
            if candidates:
                logger.info(f"CUDA DLLパスを登録しました: {len(candidates)}箇所")
        except Exception as e:
            logger.debug(f"CUDA DLLパス登録スキップ: {e}")

    def _load_whisper(self) -> bool:
        """faster-whisperモデルをロードする。成功したらTrue"""
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            logger.error("faster-whisperが見つかりません。pip install faster-whisper を実行してください")
            return False

        self._register_cuda_dlls()

        model_size = getattr(self.config, 'WHISPER_MODEL_SIZE', 'medium')
        device_pref = getattr(self.config, 'WHISPER_DEVICE', 'auto')

        # 試行順: 指定デバイス → CUDA(float16) → CPU(int8)
        attempts = []
        if device_pref == 'cuda':
            attempts = [('cuda', 'float16')]
        elif device_pref == 'cpu':
            attempts = [('cpu', 'int8')]
        else:  # auto
            attempts = [('cuda', 'float16'), ('cpu', 'int8')]

        for device, compute_type in attempts:
            try:
                logger.info(f"Whisperモデルをロード中: {model_size} / {device} / {compute_type}")
                logger.info("（初回はモデルのダウンロードが走るため数分かかることがあります）")
                self._whisper_model = WhisperModel(
                    model_size, device=device, compute_type=compute_type
                )
                logger.info(f"認識エンジン: faster-whisper {model_size}（{device.upper()}・ローカル・課金なし）")
                return True
            except Exception as e:
                logger.warning(f"Whisperロード失敗 ({device}/{compute_type}): {e}")

        return False

    # Whisperが無音から生成しがちな定番ハルシネーション（完全一致で破棄）
    WHISPER_HALLUCINATIONS = [
        "ご視聴ありがとうございました",
        "ご視聴ありがとうございました。",
        "チャンネル登録お願いします",
        "チャンネル登録お願いします。",
        "おやすみなさい",
        "ありがとうございました",
        "ありがとうございました。",
        "字幕視聴ありがとうございました",
        "最後までご視聴いただきありがとうございます",
        # v4.11: 実戦ログですり抜けた亜種を追加
        "最後までご視聴ありがとうございました",
        "最後までご視聴ありがとうございました。",
        "ご覧頂きありがとうございました",
        "ご覧頂きありがとうございました。",
        "ご覧いただきありがとうございました",
        "ご覧いただきありがとうございました。",
        "チャンネル登録よろしくお願いします",
        "チャンネル登録よろしくお願いします。",
    ]

    def _recognize_whisper(self, audio) -> str:
        """faster-whisperでAudioDataをテキスト化する"""
        import numpy as np

        raw = audio.get_raw_data(convert_rate=16000, convert_width=2)
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

        initial_prompt = getattr(
            self.config, 'WHISPER_INITIAL_PROMPT',
            "Twitchのゲーム配信。太郎、コメント太郎、フォートナイト、ビクロイ、などの言葉が出ます。"
        )
        # v4.20: 手帳の固有名詞辞書をヒントに合流させる
        if self._extra_vocab:
            initial_prompt = f"{initial_prompt} {('、'.join(self._extra_vocab))}、なども出ます。"

        segments, info = self._whisper_model.transcribe(
            samples,
            language="ja",
            beam_size=5,
            vad_filter=True,
            without_timestamps=True,
            initial_prompt=initial_prompt,
            condition_on_previous_text=False,  # ハルシネーション連鎖防止
        )

        texts = []
        for seg in segments:
            t = seg.text.strip()
            # 幻聴らしきセグメントを破棄（無音時に高頻度で出る定型文）
            if seg.no_speech_prob > 0.85:
                continue
            if t in self.WHISPER_HALLUCINATIONS:
                logger.debug(f"[幻聴フィルター] 破棄: '{t}'")
                continue
            if t:
                texts.append(t)

        return "".join(texts).strip()

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
                    if self._engine == 'whisper':
                        text = self._recognize_whisper(audio)
                        self._whisper_error_count = 0  # 成功したらリセット
                        if not text:
                            logger.debug("音声認識できませんでした（無音または雑音）")
                            continue
                    else:
                        text = recognizer.recognize_google(audio, language="ja-JP")

                    if text:
                        text = text.strip()
                        text = self._apply_corrections(text)  # v4.43: 誤変換の自動補正
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
                    logger.error(f"音声認識エラー: {e}")
                    if self._engine == 'whisper':
                        self._whisper_error_count += 1
                        if self._whisper_error_count >= 3:
                            logger.warning(
                                "Whisperエラーが3回連続したため、Google Web Speech APIに切り替えます。"
                                "（GPU関連の場合は config.py の WHISPER_DEVICE = \"cpu\" もお試しください）"
                            )
                            self._engine = 'google'
                    time.sleep(2)

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
        engine_name = "faster-whisper（ローカル）" if self._engine == 'whisper' else "Google Web Speech API"
        logger.info(f"音声認識モジュールを起動しました（{engine_name}）")

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
