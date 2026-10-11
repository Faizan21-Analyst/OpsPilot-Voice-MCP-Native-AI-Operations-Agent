from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_controller import VADController
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.services.kokoro.tts import KokoroTTSService, KokoroTTSSettings
from pipecat.services.tts_service import TextAggregationMode
from pipecat.services.groq.stt import GroqSTTService, GroqSTTSettings
from pipecat.transcriptions.language import Language
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineParams
from pipecat.pipeline.worker import PipelineWorker
from pipecat.workers.runner import WorkerRunner
# (devices.py - the local-microphone helper - is imported lazily inside build_local_voice_pipeline below)
from src.channels.voice.session import VADBridge, UtteranceAggregator
from src.channels.voice.agent_bridge import AgentBridgeBargeIn, ErrorLogger

import asyncio
import os
import threading
import time

import pipecat.services.kokoro.tts as _kokoro_module

# ---- Load the Kokoro voice engine ONCE per server process, not once per voice session ----
# KokoroTTSService() used to load the 330 MB model inside its constructor on every new voice session (~15 s of waiting).
_RealKokoro = _kokoro_module.Kokoro
_kokoro_lock = threading.Lock()
_kokoro_cache: dict = {}

# How many CPU threads the voice engine may use. onnxruntime defaults to one thread per core of the HOST, which is far
# more than the CPU a container is allowed (the gateway gets about 1.75 vCPU on Azure). The extra threads fight over
# the quota, the container is throttled, and speech stutters. 2 fits the quota; 0 = leave onnxruntime's default.
try:
    TTS_THREADS = max(0, int(os.getenv("OPSPILOT_TTS_THREADS", "2")))
except ValueError:
    TTS_THREADS = 2
# One log line per spoken sentence: how long the speech is and how long it took to make. When making it takes longer
# than playing it, the voice has gaps. Set OPSPILOT_TTS_TIMING=0 to silence these lines.
TTS_TIMING = os.getenv("OPSPILOT_TTS_TIMING", "1") != "0"


def _build_kokoro_engine(model_path, voices_path):
    """Create the Kokoro engine with a capped number of ONNX threads (falls back to the library default on any error)."""
    if TTS_THREADS > 0:
        try:
            import onnxruntime as ort
            from kokoro_onnx.session import resolve_providers

            options = ort.SessionOptions()
            options.intra_op_num_threads = TTS_THREADS
            options.inter_op_num_threads = 1
            session = ort.InferenceSession(str(model_path), sess_options=options, providers=resolve_providers())
            print(f"[voice] Kokoro runs with {TTS_THREADS} ONNX thread(s)", flush=True)
            return _RealKokoro.from_session(session, str(voices_path))
        except Exception as e:
            print(f"[voice] could not limit Kokoro threads ({e}); using the default", flush=True)
    return _RealKokoro(str(model_path), str(voices_path))


class _SharedKokoro:
    """One Kokoro engine shared by every voice session. Synthesis runs one sentence at a time because the phonemizer
    inside kokoro-onnx is not safe to call from several threads at once."""

    def __init__(self, engine):
        self._engine = engine
        self._speak_lock = None  # created on first use, inside the running event loop

    def __getattr__(self, name):
        return getattr(self._engine, name)

    async def create_stream(self, *args, **kwargs):
        if self._speak_lock is None:
            self._speak_lock = asyncio.Lock()
        async with self._speak_lock:
            started = time.perf_counter()
            first_chunk_s = None
            audio_s = 0.0
            try:
                async for item in self._engine.create_stream(*args, **kwargs):
                    if TTS_TIMING:
                        try:
                            samples, sample_rate = item
                            audio_s += len(samples) / float(sample_rate)
                            if first_chunk_s is None:
                                first_chunk_s = time.perf_counter() - started
                        except Exception:
                            pass  # timing is only a log line, never let it affect the audio
                    yield item
            finally:
                if TTS_TIMING and audio_s > 0:
                    took = time.perf_counter() - started
                    ratio = took / audio_s
                    flag = "  <-- SLOWER THAN PLAYBACK, the voice will break" if ratio > 1.0 else ""
                    print(
                        f"[voice] tts: {audio_s:.1f}s of speech made in {took:.1f}s "
                        f"({ratio:.2f}x real time, first sound after {first_chunk_s or 0:.1f}s){flag}",
                        flush=True,
                    )


def _shared_kokoro(model_path, voices_path):
    key = (str(model_path), str(voices_path))
    with _kokoro_lock:
        if key not in _kokoro_cache:
            _kokoro_cache[key] = _SharedKokoro(_build_kokoro_engine(*key))
        return _kokoro_cache[key]


# KokoroTTSService() builds its engine with Kokoro(model, voices) inside its constructor. Make that return the shared one.
_kokoro_module.Kokoro = _shared_kokoro


def preload_voice_models() -> None:
    """Run once at server start (in a worker thread). Loads the voice model and synthesises one word so the first real
    sentence does not pay for ONNX start-up. Never raises: a failure here only means the first session loads it instead."""
    try:
        started = time.perf_counter()
        model = _kokoro_module.KOKORO_CACHE_DIR / "kokoro-v1.0.onnx"
        voices = _kokoro_module.KOKORO_CACHE_DIR / "voices-v1.0.bin"
        _kokoro_module._ensure_model_files(model, voices)
        engine = _shared_kokoro(model, voices)
        engine._engine.create("Ready.", voice="af_heart", speed=1.0, lang="en-us")
        print(f"[voice] Kokoro voice model preloaded in {time.perf_counter() - started:.1f}s", flush=True)

        # Pipecat loads NLTK's sentence tokenizer (and scipy / scikit-learn with it) while every voice session starts up.
        # Do that here, once, so a session does not wait for it.
        try:
            from pipecat.utils.prewarm import warm_deferred_imports

            warm_started = time.perf_counter()
            warm_deferred_imports()
            print(f"[voice] Pipecat deferred imports warmed in {time.perf_counter() - warm_started:.1f}s", flush=True)
        except Exception as e:
            print(f"[voice] deferred-import warm-up skipped: {e}", flush=True)
    except Exception as e:
        print(f"[voice] Kokoro preload failed (it will load on the first voice session instead): {e}", flush=True)


# Tunable without a rebuild: set these in docker-compose.yml / .env and restart the gateway.
#   OPSPILOT_SILENCE_TIMEOUT : seconds of silence after your last words before the request is sent (default 1.0)
#   OPSPILOT_STT_MODEL       : Groq speech-to-text model (default whisper-large-v3; whisper-large-v3-turbo is faster)
try:
    SILENCE_TIMEOUT_S = max(0.3, float(os.getenv("OPSPILOT_SILENCE_TIMEOUT", "1.0")))
except ValueError:
    SILENCE_TIMEOUT_S = 1.0
STT_MODEL = os.getenv("OPSPILOT_STT_MODEL", "whisper-large-v3")
try:
    IDLE_TIMEOUT_S = max(30.0, float(os.getenv("OPSPILOT_IDLE_TIMEOUT_S", "240")))
except ValueError:
    IDLE_TIMEOUT_S = 240.0


def _report_slow_setup(processor, label: str, threshold_s: float = float(os.getenv("OPSPILOT_SLOW_SETUP_S", "1.0"))):
    """Print a line when a processor takes longer than `threshold_s` to set up, so a slow voice start-up shows
    exactly which step is responsible. Does not change behaviour."""
    original_setup = processor.setup

    async def timed_setup(setup):
        started = time.perf_counter()
        try:
            await original_setup(setup)
        finally:
            took = time.perf_counter() - started
            if took >= threshold_s:
                print(f"[voice] slow start-up: {label} took {took:.1f}s to set up", flush=True)

    processor.setup = timed_setup

async def build_voice_pipeline(settings, agent, manager, principal: dict, session_id: str, transport):
    """
    Assembles the pipeline around a transport that's passed in — the
    caller decides whether that's a local mic (devices.py) or a browser
    WebSocket (voice_ws.py). Everything else (VAD, STT, agent bridge,
    TTS) is identical either way.
    """
    vad_analyzer = SileroVADAnalyzer(
        sample_rate=16000,
        params=VADParams(
            confidence=0.6,
            start_secs=0.15,
            stop_secs=0.35,
            min_volume=0.5,
        ),
    )

    vad_controller = VADController(
        vad_analyzer,
        speech_activity_period=0.2,
        audio_idle_timeout=1.5,
    )

    vad_bridge = VADBridge(vad_controller)

    stt_settings = GroqSTTSettings(
        model=STT_MODEL,
        language=Language.EN,
        temperature=0.0,
        prompt=(
            "This is an IT operations voice conversation. "
            "Transcribe exactly what the speaker says. "
            "Preserve names, departments, technical terms, "
            "commands and numbers accurately. "
            "Important terms: OpsPilot, revoke access, "
            "reset password, password reset policy, create ticket, "
            "employee, employee ID, Engineering department, "
            "admin, MCP, Prometheus, Grafana, Jira, SQL table, "
            "previous month's sales. "
            "Employee names: Bob, Alice."
        ),
    )

    stt = GroqSTTService(
        api_key=settings.groq.api_key,
        settings=stt_settings,
    )

    tts = KokoroTTSService(
        settings=KokoroTTSSettings(
            voice="af_heart",
        ),
        text_aggregation_mode=TextAggregationMode.TOKEN,
        max_consecutive_zero_audio_contexts=10,
    )

    agent_bridge = AgentBridgeBargeIn(
        agent=agent,
        manager=manager,
        principal=principal,
        session_id=session_id,
    )

    utterance_aggregator = UtteranceAggregator(silence_timeout=SILENCE_TIMEOUT_S)

    for _label, _processor in (
        ("VAD", vad_bridge),
        ("speech-to-text", stt),
        ("utterance aggregator", utterance_aggregator),
        ("agent bridge", agent_bridge),
        ("text-to-speech", tts),
    ):
        _report_slow_setup(_processor, _label)

    pipeline = Pipeline(
        [
            transport.input(),
            vad_bridge,
            stt,
            utterance_aggregator,
            agent_bridge,
            tts,
            ErrorLogger(),
            transport.output(),
        ]
    )

    # setup_timeout_secs: Pipecat's default is 20 s for ALL processors to set up. On a busy or slow container (Docker on
    # Windows) that is too tight, and the whole voice session is stopped when it is exceeded. 60 s leaves plenty of room.
    # idle_timeout_secs: end the voice session after this long with nobody speaking or being spoken to. On Azure this
    # is also the cost guard: an open browser tab would otherwise keep the (billed) container awake. Adjustable with
    # OPSPILOT_IDLE_TIMEOUT_S (seconds, default 240).
    worker = PipelineWorker(pipeline, params=PipelineParams(), setup_timeout_secs=60.0, idle_timeout_secs=IDLE_TIMEOUT_S)
    runner = WorkerRunner()
    await runner.add_workers(worker)

    print()
    print("=" * 70)
    print("VOICE PIPELINE READY")
    print("=" * 70)
    print(f"Session: {session_id}")
    print("=" * 70)
    print()

    return pipeline, worker, runner


async def build_local_voice_pipeline(settings, agent, manager, principal: dict, session_id: str):
    """Wraps build_voice_pipeline with a LocalAudioTransport for the
    local mic/speaker mode (run_voice.py)."""
    from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams
    from src.channels.voice.devices import select_audio_devices
    input_device_index, input_sample_rate, output_device_index = select_audio_devices()

    transport = LocalAudioTransport(
        LocalAudioTransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            input_device_index=input_device_index,
            output_device_index=output_device_index,
            audio_in_sample_rate=input_sample_rate,
        )
    )

    print(f"Input device : index {input_device_index} @ {input_sample_rate} Hz")
    print(f"Output device: index {output_device_index}")

    return await build_voice_pipeline(settings, agent, manager, principal, session_id, transport)