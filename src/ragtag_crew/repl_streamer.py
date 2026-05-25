"""ReplStreamer — streams agent events to the terminal in real-time.

Mirrors the event coverage of TelegramStreamer but writes to stdout
instead of Telegram message edits.
"""

from __future__ import annotations

from ragtag_crew.runtime_events import (
    AgentEndEvent,
    CancelledEvent,
    ErrorEvent,
    MessageEndEvent,
    MessageUpdateEvent,
    RuntimeEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
)


def _summarize_args(args: dict) -> str:
    parts = []
    for k, v in args.items():
        s = str(v)
        if len(s) > 40:
            s = s[:37] + "..."
        parts.append(f"{k}={s}")
    return ", ".join(parts)


class ReplStreamer:
    """Receives agent events and prints them to the terminal in real-time.

    Usage::

        streamer = ReplStreamer()
        session.subscribe(streamer.on_event)
        await session.prompt(text)
        session.unsubscribe(streamer.on_event)
    """

    def __init__(self) -> None:
        self.buffer = ""

    async def on_event(self, event: RuntimeEvent) -> None:
        match event:
            case MessageUpdateEvent(delta=delta):
                self.buffer += delta
                print(delta, end="", flush=True)

            case MessageEndEvent(content=content):
                if content and not self.buffer.strip():
                    self.buffer = content
                    print(content, end="", flush=True)

            case ToolExecutionStartEvent(tool_call=tc):
                args_str = _summarize_args(tc.arguments)
                print(f"\n⏳ {tc.name}({args_str})")

            case ToolExecutionEndEvent(result=result):
                if result:
                    preview = result[:200].replace("\n", " ")
                    suffix = "..." if len(result) > 200 else ""
                    print(f"  ✅ → {preview}{suffix}")

            case AgentEndEvent():
                if self.buffer:
                    print()

            case CancelledEvent():
                print("\n⚠️ 已取消")

            case ErrorEvent(error=error):
                print(f"\n❌ {error}")
