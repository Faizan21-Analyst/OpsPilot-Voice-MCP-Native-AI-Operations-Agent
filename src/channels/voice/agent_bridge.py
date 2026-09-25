import re
import random
import traceback
import asyncio
from pipecat.frames.frames import (
    ErrorFrame,
    Frame,
    TranscriptionFrame,
    TextFrame,
    LLMFullResponseStartFrame,
    LLMFullResponseEndFrame,
    InterruptionFrame,
    StartFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from langgraph.types import Command


def strip_markdown_for_speech(text: str) -> str:
    text = re.sub(r"\|.*\|", "", text)
    text = re.sub(r"^[-|: ]+$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"\*(.*?)\*", r"\1", text)
    text = re.sub(r"^\s*[-*]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"[`#>]", "", text)
    text = text.replace("—", ",").replace("–", ",")
    text = text.replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if text and text[-1] not in ".!?":
        text += "."
    return text


def split_into_sentences(text: str, min_len: int = 15) -> list[str]:
    raw = re.split(r"(?<=[.!?])\s+", text)
    raw = [s.strip() for s in raw if s.strip()]

    merged = []
    for sentence in raw:
        if merged and len(sentence) < min_len:
            merged[-1] = merged[-1] + " " + sentence
        else:
            merged.append(sentence)
    return merged


class AgentBridge(FrameProcessor):
    """Base bridge: blocking, no barge-in. Kept for simple/test use."""

    def __init__(self, agent, manager, principal: dict, session_id: str):
        super().__init__()
        self.agent = agent
        self.manager = manager
        self.principal = principal
        self.session_id = session_id
        self._config = {"configurable": {"thread_id": session_id}}
        self._available_tools = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame):
            text = frame.text.strip()
            if not text:
                return

            print()
            print("=" * 60)
            print("[FINAL REQUEST]")
            print(text)
            print("=" * 60)

            if self._available_tools is None:
                self._available_tools = await self.manager.discover_tools()

            initial_state = {
                "messages": [{"role": "user", "content": text}],
                "session_id": self.session_id,
                "principal": dict(self.principal),
                "available_tools": self._available_tools,
            }

            try:
                result = await self.agent.ainvoke(initial_state, config=self._config)

                if "__interrupt__" in result:
                    interrupt_payload = result["__interrupt__"][0].value
                    print(f"\n[VOICE APPROVAL NEEDED] {interrupt_payload}")
                    answer = input("Approve? (y/n): ").strip().lower()
                    result = await self.agent.ainvoke(
                        Command(resume={"approved": answer == "y"}),
                        config=self._config,
                    )

                final_message = result["messages"][-1]
                answer_text = (
                    final_message.get("content")
                    if isinstance(final_message, dict)
                    else final_message.content
                )

                if not answer_text:
                    answer_text = "I'm sorry, I didn't get a response for that. Could you try again?"

            except Exception:
                print("\n[AgentBridge ERROR] agent invocation failed:")
                traceback.print_exc()
                answer_text = "Sorry, something went wrong processing that request."

            print(f"\n[AGENT RESPONSE] {answer_text}")
            print("=" * 60)
            print()

            speech_text = strip_markdown_for_speech(answer_text)
            sentences = split_into_sentences(speech_text)

            for sentence in sentences:
                await self.push_frame(TextFrame(text=sentence), FrameDirection.DOWNSTREAM)

            return

        await self.push_frame(frame, direction)


class AgentBridgeBargeIn(AgentBridge):
    """
    Production bridge: runs the agent call as a cancellable background
    task so real barge-in works, speaks a filler immediately for
    perceived latency, and greets on startup.
    """

    _FILLERS = [
        "Okay, let me check that for you.",
        "Sure, one moment while I look into it.",
        "Got it, working on that now.",
        "Alright, let me pull that up.",
        "One second, I'm on it.",
    ]

    _GREETING = (
        "Hello! I'm OpsPilot, your IT operations assistant. "
        "How can I help you today?"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._response_task = None
        self._greeted = False

    async def setup(self, setup):
        await super().setup(setup)
        try:
            self._available_tools = await self.manager.discover_tools()
            print("[AgentBridge] MCP tools pre-warmed at startup")
        except Exception:
            print("[AgentBridge] tool pre-warm failed, will retry on first request")
            traceback.print_exc()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await FrameProcessor.process_frame(self, frame, direction)

        if isinstance(frame, StartFrame) and not self._greeted:
            self._greeted = True
            self._response_task = self.create_task(self._speak(self._GREETING))

        if isinstance(frame, InterruptionFrame):
            await self._cancel_current_response("barge-in (InterruptionFrame)")
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TranscriptionFrame):
            text = frame.text.strip()
            if not text:
                return

            await self._cancel_current_response("new utterance arrived")
            self._response_task = self.create_task(self._handle_request(text))
            return

        await self.push_frame(frame, direction)

    async def _cancel_current_response(self, reason: str):
        if self._response_task and not self._response_task.done():
            print(f"\n[AgentBridge] cancelling in-flight response ({reason})")
            await self.cancel_task(self._response_task)
        self._response_task = None

    async def _handle_request(self, text: str):
        print()
        print("=" * 60)
        print("[FINAL REQUEST]")
        print(text)
        print("=" * 60)

        await self._speak(random.choice(self._FILLERS))

        if self._available_tools is None:
            self._available_tools = await self.manager.discover_tools()

        initial_state = {
            "messages": [{"role": "user", "content": text}],
            "session_id": self.session_id,
            "principal": dict(self.principal),
            "available_tools": self._available_tools,
        }

        try:
            result = await self.agent.ainvoke(initial_state, config=self._config)

            if "__interrupt__" in result:
                interrupt_payload = result["__interrupt__"][0].value
                print(f"\n[VOICE APPROVAL NEEDED] {interrupt_payload}")
                answer = input("Approve? (y/n): ").strip().lower()
                result = await self.agent.ainvoke(
                    Command(resume={"approved": answer == "y"}),
                    config=self._config,
                )

            final_message = result["messages"][-1]
            answer_text = (
                final_message.get("content")
                if isinstance(final_message, dict)
                else final_message.content
            )

            if not answer_text:
                answer_text = "I'm sorry, I didn't get a response for that. Could you try again?"

        except asyncio.CancelledError:
            raise
        except Exception:
            print("\n[AgentBridge ERROR] agent invocation failed:")
            traceback.print_exc()
            answer_text = "Sorry, something went wrong processing that request."

        print(f"\n[AGENT RESPONSE] {answer_text}")
        print("=" * 60)
        print()

        await self._speak(answer_text)

    async def _speak(self, text: str):
        speech_text = strip_markdown_for_speech(text)
        sentences = split_into_sentences(speech_text)

        await self.push_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
        try:
            for sentence in sentences:
                await self.push_frame(TextFrame(text=sentence), FrameDirection.DOWNSTREAM)
        finally:
            await self.push_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)


class ErrorLogger(FrameProcessor):
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, ErrorFrame):
            print(f"\n[TTS ERROR DETAIL] {frame.error}\n")
        await self.push_frame(frame, direction)