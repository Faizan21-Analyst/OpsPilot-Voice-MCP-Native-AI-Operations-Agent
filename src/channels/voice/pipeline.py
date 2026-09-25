from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_controller import VADController
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.services.kokoro.tts import KokoroTTSService, KokoroTTSSettings
from pipecat.services.tts_service import TextAggregationMode
from pipecat.services.groq.stt import GroqSTTService, GroqSTTSettings
from pipecat.transcriptions.language import Language
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineParams
from pipecat.pipeline.worker import PipelineWorker
from pipecat.workers.runner import WorkerRunner

from src.channels.voice.devices import select_audio_devices
from src.channels.voice.session import VADBridge, UtteranceAggregator
from src.channels.voice.agent_bridge import AgentBridgeBargeIn, ErrorLogger


async def build_voice_pipeline(settings, agent, manager, principal: dict, session_id: str):
    """
    Assembles the full voice pipeline: device detection, VAD, STT,
    the agent bridge (barge-in capable), TTS, and audio output.

    Returns (pipeline, worker, runner) — caller is responsible for
    calling runner.run().
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

    try:
        input_device_index, input_sample_rate, output_device_index = select_audio_devices()
    except RuntimeError as e:
        raise RuntimeError(f"Voice pipeline audio setup failed: {e}") from e

    transport = LocalAudioTransport(
        LocalAudioTransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            input_device_index=input_device_index,
            output_device_index=output_device_index,
            audio_in_sample_rate=input_sample_rate,
        )
    )

    stt_settings = GroqSTTSettings(
        model="whisper-large-v3",
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

    utterance_aggregator = UtteranceAggregator(silence_timeout=1.8)

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

    worker = PipelineWorker(pipeline, params=PipelineParams())
    runner = WorkerRunner()
    await runner.add_workers(worker)

    print()
    print("=" * 70)
    print("VOICE PIPELINE READY")
    print("=" * 70)
    print(f"Input device : index {input_device_index} @ {input_sample_rate} Hz")
    print(f"Output device: index {output_device_index}")
    print("=" * 70)
    print()

    return pipeline, worker, runner