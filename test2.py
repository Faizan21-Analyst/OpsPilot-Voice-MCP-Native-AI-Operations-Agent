import asyncio
import os

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import TranscriptionFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineParams
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.groq.stt import GroqSTTService
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams

from pipecat.pipeline.worker import PipelineWorker
from pipecat.workers.runner import WorkerRunner

from src.core.config import load_settings


class PrintTranscript(FrameProcessor):
    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame):
            print(f"\n>>> TRANSCRIPT: {frame.text}\n")
        await self.push_frame(frame, direction)


async def main():
    settings = load_settings()

    transport = LocalAudioTransport(
    LocalAudioTransportParams(
        audio_in_enabled=True,
        audio_out_enabled=False,
        vad_analyzer=SileroVADAnalyzer(),
    )
)

    stt = GroqSTTService(api_key=settings.groq.api_key)

    pipeline = Pipeline([
        transport.input(),
        stt,
        PrintTranscript(),
    ])

    task = PipelineWorker(pipeline, params=PipelineParams())
    runner = WorkerRunner()
    
    await runner.add_workers(task)

    print("Speak into your mic. Ctrl+C to stop.")
    await runner.run()


if __name__ == "__main__":
    asyncio.run(main())