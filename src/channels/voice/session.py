import asyncio
import traceback

from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    TranscriptionFrame,
    InterruptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.audio.vad.vad_controller import VADController


class VADBridge(FrameProcessor):

    def __init__(self, vad_controller: VADController):
        super().__init__()
        self.vad_controller = vad_controller
        self._last_level_print = 0.0

    async def setup(self, setup):
        await super().setup(setup)
        await self.vad_controller.setup(setup)

        self.vad_controller._event_handlers[
            "on_speech_started"
        ].handlers.append(self._on_speech_started)

        self.vad_controller._event_handlers[
            "on_speech_stopped"
        ].handlers.append(self._on_speech_stopped)

    async def start(self):
        await super().start()
        await self.vad_controller.start()

    async def stop(self):
        await self.vad_controller.stop()
        await super().stop()

    async def cleanup(self):
        await self.vad_controller.cleanup()
        await super().cleanup()

    async def _on_speech_started(self, *args, **kwargs):
        print("\n[VAD EVENT] SPEECH STARTED")
        await self.push_frame(InterruptionFrame())
        await self.push_frame(VADUserStartedSpeakingFrame())

    async def _on_speech_stopped(self, *args, **kwargs):
        print("\n[VAD EVENT] SPEECH STOPPED")
        await self.push_frame(VADUserStoppedSpeakingFrame())

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, InputAudioRawFrame):
            self._print_level_if_due(frame)
            await self.vad_controller.process_frame(frame)

        await self.push_frame(frame, direction)

    def _print_level_if_due(self, frame: InputAudioRawFrame):
        now = asyncio.get_event_loop().time()
        if now - self._last_level_print < 1.0:
            return
        self._last_level_print = now

        try:
            import array
            samples = array.array("h", frame.audio)
            peak = max(abs(s) for s in samples) if samples else 0
        except Exception:
            peak = -1

        if peak <= 0:
            print("[audio level] SILENT (all-zero samples) - mic is not "
                  "actually capturing audio, even though the stream opened.")
        else:
            bar = "#" * min(40, peak // 800)
            print(f"[audio level] peak={peak:>6} {bar}")


class UtteranceAggregator(FrameProcessor):

    def __init__(self, silence_timeout: float = 1.8):
        super().__init__()
        self.silence_timeout = silence_timeout
        self._fragments: list[str] = []
        self._last_frame: TranscriptionFrame | None = None
        self._timer_task: asyncio.Task | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame):
            text = frame.text.strip()
            if text:
                print(f"[fragment] {text}")
                self._fragments.append(text)
                self._last_frame = frame
                await self._restart_timer()
            return

        await self.push_frame(frame, direction)

    async def _restart_timer(self):
        if self._timer_task:
            await self.cancel_task(self._timer_task)
        self._timer_task = self.create_task(self._commit_after_silence())

    async def _commit_after_silence(self):
        try:
            await asyncio.sleep(self.silence_timeout)
        except asyncio.CancelledError:
            return

        print("[aggregator] silence timeout reached, committing...")

        if not self._fragments or self._last_frame is None:
            return

        final_text = " ".join(self._fragments).strip()
        self._fragments = []

        try:
            final_frame = TranscriptionFrame(
                text=final_text,
                user_id=self._last_frame.user_id,
                timestamp=self._last_frame.timestamp,
                language=self._last_frame.language,
            )
            print(f"[aggregator] pushing final frame: {final_text!r}")
            await self.push_frame(final_frame, FrameDirection.DOWNSTREAM)
            print("[aggregator] push_frame returned normally")
        except Exception:
            print("\n[UtteranceAggregator ERROR] failed to commit final utterance:")
            traceback.print_exc()
        finally:
            self._last_frame = None

    async def cleanup(self):
        if self._timer_task:
            await self.cancel_task(self._timer_task)
        await super().cleanup()