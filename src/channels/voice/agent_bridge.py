import re
import random
import traceback
import asyncio
from pipecat.frames.frames import (
    ErrorFrame,
    Frame,
    TranscriptionFrame,
    LLMTextFrame,
    TextFrame,
    LLMFullResponseStartFrame,
    LLMFullResponseEndFrame,
    InterruptionFrame,
    StartFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi.frames import RTVIServerMessageFrame
from langgraph.types import Command


def strip_markdown_for_speech(text: str) -> str:
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)  # never read code/diagram blocks aloud
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


# ---------------------------------------------------------------------------
# Spoken yes/no parsing for voice approvals
# ---------------------------------------------------------------------------
_YES_WORDS = {
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "approve", "approved",
    "confirm", "confirmed", "proceed", "correct", "affirmative",
}
_YES_PHRASES = ("go ahead", "do it", "go for it")
_NO_WORDS = {
    "no", "nope", "nah", "deny", "denied", "cancel", "stop", "reject",
    "decline", "negative", "dont",
}
_NO_PHRASES = ("do not", "don't", "dont", "hold off")


def parse_yes_no(text: str) -> bool | None:
    """True = approved, False = denied, None = couldn't tell."""
    lowered = text.lower().replace("’", "'")
    words = set(re.findall(r"[a-z']+", lowered))

    # Check "no" first so "no, don't do it" never reads as approval.
    if words & _NO_WORDS or any(p in lowered for p in _NO_PHRASES):
        return False
    if words & _YES_WORDS or any(p in lowered for p in _YES_PHRASES):
        return True
    return None


def build_approval_prompt(payload) -> str:
    """Turn the executor's interrupt payload into something speakable."""
    if not isinstance(payload, dict):
        return "I need your approval to continue. Should I go ahead? Please say yes or no."

    tool = str(payload.get("tool", "this action")).replace("_", " ")
    hidden = {"requested_by", "requester_role", "idempotency_key"}
    args = payload.get("arguments") or {}
    details = ", ".join(
        f"{str(k).replace('_', ' ')} {v}" for k, v in args.items() if k not in hidden
    )

    prompt = f"I need your approval to run {tool}"
    if details:
        prompt += f" with {details}"
    prompt += ". Should I go ahead? Please say yes or no."
    return prompt


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
                # LLMTextFrame (a TextFrame subclass) is spoken by TTS AND
                # reported to the browser as a bot transcript by RTVI.
                await self.push_frame(
                    LLMTextFrame(text=sentence + " "), FrameDirection.DOWNSTREAM
                )

            return

        await self.push_frame(frame, direction)


class AgentBridgeBargeIn(AgentBridge):
    """
    Production bridge: runs the agent call as a cancellable background
    task so real barge-in works, speaks a filler immediately for
    perceived latency, and greets on startup.

    Approvals are handled by voice (no terminal input()): the bot speaks
    what needs approving, and the user's next utterance ("yes" / "no")
    resumes the paused LangGraph run.
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
        # Interrupt payload while we wait for a spoken yes/no, else None.
        self._pending_approval = None

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

            # A paused approval is waiting: this utterance is the answer.
            if self._pending_approval is not None:
                decision = parse_yes_no(text)
                if decision is None:
                    self._response_task = self.create_task(
                        self._speak("Sorry, I didn't catch that. Should I go ahead? Please say yes or no.")
                    )
                else:
                    self._response_task = self.create_task(self._handle_approval(decision))
                return

            self._response_task = self.create_task(self._handle_request(text))
            return

        await self.push_frame(frame, direction)

    async def _cancel_current_response(self, reason: str):
        if self._response_task and not self._response_task.done():
            print(f"\n[AgentBridge] cancelling in-flight response ({reason})")
            await self.cancel_task(self._response_task)
        self._response_task = None

    async def _run_agent(self, agent_input) -> str:
        """Invoke the agent; return the text to speak (answer or approval prompt)."""
        try:
            result = await self.agent.ainvoke(agent_input, config=self._config)

            if "__interrupt__" in result:
                payload = result["__interrupt__"][0].value
                print(f"\n[VOICE APPROVAL NEEDED] {payload}")
                self._pending_approval = payload
                return build_approval_prompt(payload)

            final_message = result["messages"][-1]
            answer_text = (
                final_message.get("content")
                if isinstance(final_message, dict)
                else final_message.content
            )
            if not answer_text:
                answer_text = "I'm sorry, I didn't get a response for that. Could you try again?"
            return answer_text

        except asyncio.CancelledError:
            raise
        except Exception:
            print("\n[AgentBridge ERROR] agent invocation failed:")
            traceback.print_exc()
            self._pending_approval = None
            return "Sorry, something went wrong processing that request."

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

        answer_text = await self._run_agent(initial_state)

        print(f"\n[AGENT RESPONSE] {answer_text}")
        print("=" * 60)
        print()

        await self._deliver(answer_text)

    async def _handle_approval(self, approved: bool):
        print(f"\n[VOICE APPROVAL DECISION] approved={approved}")
        self._pending_approval = None

        await self._speak("Approved, running it now." if approved else "Okay, cancelling that.")

        answer_text = await self._run_agent(Command(resume={"approved": approved}))

        print(f"\n[AGENT RESPONSE] {answer_text}")
        print("=" * 60)
        print()

        await self._deliver(answer_text)

    async def _deliver(self, answer_text: str):
        """Send the agent's reply: full markdown to the page, spoken version to TTS."""
        if self._pending_approval is not None:
            # An approval question, not an answer: speak it and show it as a normal transcript.
            await self._speak(answer_text)
        else:
            await self._speak(answer_text, as_answer=True)

    async def _speak(self, text: str, as_answer: bool = False):
        """
        as_answer=False: spoken via LLMTextFrame -> RTVI also shows it as a bot transcript
                         (greeting, fillers, approval questions).
        as_answer=True:  the FULL markdown (tables, lists, diagrams) is sent to the page as an
                         RTVI server message, and only a speech-friendly version is spoken
                         (TextFrame -> no duplicate transcript bubble).
        """
        speech_text = strip_markdown_for_speech(text)

        if as_answer:
            has_visuals = (
                bool(re.search(r"^\s*\|.*\|\s*$", text, flags=re.MULTILINE)) or "```" in text
            )
            await self.push_frame(
                RTVIServerMessageFrame(data={"type": "agent_answer", "text": text}),
                FrameDirection.DOWNSTREAM,
            )
            if has_visuals:
                speech_text = (speech_text + " " if speech_text else "") + "I've put the full details on your screen."

        sentences = split_into_sentences(speech_text)
        text_frame_cls = TextFrame if as_answer else LLMTextFrame

        await self.push_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
        try:
            for sentence in sentences:
                # The trailing space keeps sentences from running together when RTVI joins them.
                await self.push_frame(
                    text_frame_cls(text=sentence + " "), FrameDirection.DOWNSTREAM
                )
        finally:
            await self.push_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)

class ErrorLogger(FrameProcessor):
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, ErrorFrame):
            print(f"\n[TTS ERROR DETAIL] {frame.error}\n")
        await self.push_frame(frame, direction)
