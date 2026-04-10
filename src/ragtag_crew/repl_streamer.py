"""ReplStreamer — streams agent events to the terminal in real-time.

Mirrors the event coverage of TelegramStreamer but writes to stdout
instead of Telegram message edits.
"""

from __future__ import annotations

from ragtag_crew.llm import ToolCall


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

    async def on_event(self, event_type: str, **kwargs) -> None:
        match event_type:
            case "message_update":
                delta = kwargs["delta"]
                self.buffer += delta
                print(delta, end="", flush=True)

            case "message_end":
                content = kwargs.get("content", "")
                if content and not self.buffer.strip():
                    self.buffer = content
                    print(content, end="", flush=True)

            case "tool_execution_start":
                tc: ToolCall = kwargs["tool_call"]
                args_str = _summarize_args(tc.arguments)
                print(f"\n⏳ {tc.name}({args_str})")

            case "tool_execution_end":
                result = kwargs.get("result", "")
                if result:
                    preview = result[:200].replace("\n", " ")
                    suffix = "..." if len(result) > 200 else ""
                    print(f"  ✅ → {preview}{suffix}")

            case "agent_end":
                if self.buffer:
                    print()

            case "cancelled":
                print("\n⚠️ 已取消")

            case "error":
                print(f"\n❌ {kwargs.get('error', 'Unknown error')}")
