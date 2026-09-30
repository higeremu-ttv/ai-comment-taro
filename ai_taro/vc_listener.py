"""
VCの声を聞く係 v4.59（改造仕様§5 VCモード）

ゲーム内VC（フォートナイト）の声を、太郎の「2本目の耳」として聞く。
- Windowsの出力先（既定: Wave Link の「Voice Chat (Elgato XLR Dock)」）に流れる音を、
  SoundCard のループバック（出力の横取り録音）で拾う。仮想ケーブルは不要。
  フォートナイト側で「ボイスチャットの出力デバイス」をその出力先にしておく必要がある。
  （2026-09-30: この出力先へ流した音を横取りしてWhisperで正しく文字起こしできることを実機で確認）
- 聞き取りはマイクと同じWhisperモデルを使い回す（AudioModule.transcribe_samples）。VRAMは増えない。
- 声の区切りは音の大きさで判断する（一定以上の大きさが続いたら発話、静かになったら区切る）。
- 誰が話したかは区別できないので「VCの誰か」として扱う。
- 起動時はオフ。GUIのボタンか声（「太郎、VC聞いて」「太郎、VCはいいよ」）で切り替える。

録音（出力の横取り）と文字起こしは別のスレッドにして、文字起こし中も録音を止めない。
"""

import logging
import queue
import threading
import time

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
BLOCK_SEC = 0.1


def _patch_numpy_fromstring():
    """SoundCard 0.4.5 は numpy.fromstring（numpy 2 で廃止された使い方）を内部で使うため、
    同じ動きをする frombuffer に差し替える（2026-09-30 実機で ValueError が出たため）"""
    import numpy as np
    if getattr(np, "_taro_fromstring_patched", False):
        return
    np.fromstring = lambda s, dtype=float, *a, **k: np.frombuffer(s, dtype=dtype).copy()
    np._taro_fromstring_patched = True


def looks_like_hallucination(text: str) -> bool:
    """Whisperが雑音から作りがちな「同じ音の延々とした繰り返し」を見分ける。
    例:「チョチョチョチョ…」（2026-10-01 実際のVCで発生）。1〜3文字のかたまりが5回以上続けば捨てる"""
    import re
    return re.search(r"(.{1,3})\1{4,}", text) is not None


def find_loopback_device(name_part: str):
    """名前の一部が一致する出力先の「横取り録音」口を探す。見つからなければ None"""
    _patch_numpy_fromstring()
    import soundcard as sc
    for m in sc.all_microphones(include_loopback=True):
        if m.isloopback and name_part in m.name:
            return m
    return None


class VCWhisper:
    """v4.59: VCのふだんの文字起こし専用のWhisper（配信者のマイク用とは別の実体）。
    同じモデルを2か所で順番待ちして、配信者の声の聞き取りが遅れた（2026-10-01の配信）ため分けた。
    ふだんは合図の言葉（返事して・太郎 等）を拾えれば足りるので、既定は軽めの medium。
    初めて使うときに読み込む（数秒かかる）"""

    def __init__(self, model_size="medium", initial_prompt="", device="cuda", compute_type="float16"):
        self.model_size = model_size
        self.initial_prompt = initial_prompt
        self._device = device
        self._compute_type = compute_type
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        from faster_whisper import WhisperModel
        for dev, ct in ((self._device, self._compute_type), ("cpu", "int8")):
            try:
                self._model = WhisperModel(self.model_size, device=dev, compute_type=ct)
                logger.info(f"[VC] VC用のWhisperを読み込みました（{self.model_size} / {dev}）")
                return
            except Exception as e:
                logger.warning(f"[VC] VC用Whisperの読み込み失敗（{dev}）: {e}")

    def transcribe(self, samples) -> str:
        with self._lock:
            if self._model is None:
                self._load()
            if self._model is None:
                return ""
            segments, _ = self._model.transcribe(
                samples, language="ja", beam_size=3, vad_filter=True, without_timestamps=True,
                initial_prompt=self.initial_prompt or None, condition_on_previous_text=False)
            return "".join(s.text.strip() for s in segments if s.no_speech_prob <= 0.85).strip()


class Utterance:
    """音の大きさで発話を区切る（録音ブロックを順に渡すと、区切れたときに発話を返す）"""

    def __init__(self, threshold=0.01, end_silence_sec=0.8, min_voiced_sec=0.4, max_sec=15.0):
        self.threshold = threshold
        # round: 0.3/0.1 が 2.999… になり int() で切り捨てられるのを防ぐ
        self.end_blocks = max(1, round(end_silence_sec / BLOCK_SEC))
        self.min_voiced_blocks = max(1, round(min_voiced_sec / BLOCK_SEC))
        self.max_blocks = max(1, round(max_sec / BLOCK_SEC))
        self._blocks = []
        self._voiced = 0
        self._silent_run = 0

    def feed(self, block):
        """block: 1次元のfloat配列（BLOCK_SEC ぶん）。発話が区切れたらその音声を返す。それ以外は None"""
        import numpy as np
        rms = float(np.sqrt(np.mean(block ** 2))) if len(block) else 0.0
        loud = rms >= self.threshold
        if not self._blocks and not loud:
            return None  # 発話が始まっていない
        self._blocks.append(block)
        if loud:
            self._voiced += 1
            self._silent_run = 0
        else:
            self._silent_run += 1
        if self._silent_run >= self.end_blocks or len(self._blocks) >= self.max_blocks:
            voiced, blocks = self._voiced, self._blocks
            self._blocks, self._voiced, self._silent_run = [], 0, 0
            if voiced >= self.min_voiced_blocks:
                return np.concatenate(blocks)
        return None


class VCListener:

    def __init__(self, config, transcribe, on_text, device_finder=find_loopback_device):
        """transcribe: 16kHz float32 → 文字列（AudioModule.transcribe_samples）
        on_text: 聞き取れた文字列を受け取る（LaneManager.on_vc_speech）"""
        self.config = config
        self._transcribe = transcribe
        self._on_text = on_text
        self._find = device_finder
        self.device_name = getattr(config, "VC_DEVICE_NAME", "Voice Chat")
        self.min_chars = getattr(config, "VC_MIN_CHARS", 4)
        self._utt_args = dict(
            threshold=getattr(config, "VC_ENERGY_THRESHOLD", 0.01),
            end_silence_sec=getattr(config, "VC_END_SILENCE_SECONDS", 0.8),
        )
        self.listening = False
        self._stop = threading.Event()
        self._jobs = queue.Queue(maxsize=10)
        self._threads = []
        # v4.59: 直近の音を取っておく（合図が出たら、さかのぼって精度の高いWhisperで聞き直すため）
        from collections import deque
        self.lookback_sec = getattr(config, "VC_LOOKBACK_SECONDS", 30)
        self._recent = deque(maxlen=max(1, round(self.lookback_sec / BLOCK_SEC)))
        self._recent_lock = threading.Lock()

    # ------------------------------------------------------------
    # 切り替え（GUIのボタン・声の命令から呼ばれる）
    # ------------------------------------------------------------
    def set_listening(self, on: bool) -> bool:
        """聞く／聞かないを切り替える。聞き始められたら True（出力先が見つからない等で失敗なら False）"""
        if on and not self.listening:
            dev = self._find(self.device_name)
            if dev is None:
                logger.warning(f"[VC] 出力先「{self.device_name}」が見つからないため、VCを聞けません")
                return False
            self._stop.clear()
            self.listening = True
            self._threads = [
                threading.Thread(target=self._capture_loop, args=(dev,), daemon=True),
                threading.Thread(target=self._transcribe_loop, daemon=True),
            ]
            for t in self._threads:
                t.start()
            logger.info(f"[VC] VCを聞き始めました（{dev.name}）")
            return True
        if not on and self.listening:
            self.listening = False
            self._stop.set()
            try:
                self._jobs.put_nowait(None)
            except queue.Full:
                pass
            for t in self._threads:
                t.join(timeout=3)
            self._threads = []
            logger.info("[VC] VCを聞くのをやめました")
        return self.listening == on

    def stop(self):
        self.set_listening(False)

    def get_recent_audio(self):
        """直近 lookback_sec 秒ぶんの音（16kHz float32）。聞いていない・まだ無ければ None"""
        import numpy as np
        with self._recent_lock:
            blocks = list(self._recent)
        if not blocks:
            return None
        return np.concatenate(blocks)

    # ------------------------------------------------------------
    # 録音（出力の横取り）
    # ------------------------------------------------------------
    def _capture_loop(self, dev):
        utt = Utterance(**self._utt_args)
        frames = int(SAMPLE_RATE * BLOCK_SEC)
        try:
            with dev.recorder(samplerate=SAMPLE_RATE, channels=1) as rec:
                while not self._stop.is_set():
                    block = rec.record(numframes=frames).reshape(-1).astype("float32")
                    with self._recent_lock:
                        self._recent.append(block)
                    audio = utt.feed(block)
                    if audio is not None:
                        try:
                            self._jobs.put_nowait(audio)
                        except queue.Full:
                            logger.info("[VC] 文字起こしが追いつかないため、1つ飛ばしました")
        except Exception as e:
            logger.warning(f"[VC] 録音エラー: {e}")
            self.listening = False

    # ------------------------------------------------------------
    # 文字起こし
    # ------------------------------------------------------------
    def _transcribe_loop(self):
        while not self._stop.is_set():
            audio = self._jobs.get()
            if audio is None:
                break
            try:
                text = (self._transcribe(audio) or "").strip()
            except Exception as e:
                logger.warning(f"[VC] 文字起こしエラー: {e}")
                continue
            if len(text) < self.min_chars:
                continue
            if looks_like_hallucination(text):
                logger.info(f"[VC] 聞き間違いらしい繰り返しのため捨てました: {text[:20]}…")
                continue
            try:
                self._on_text(text)
            except Exception as e:
                logger.warning(f"[VC] 反応処理エラー: {e}")
