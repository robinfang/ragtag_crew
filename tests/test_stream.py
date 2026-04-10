from __future__ import annotations

import asyncio
import unittest

from telegram.error import BadRequest

from ragtag_crew.telegram.stream import TelegramStreamer


class FakeReplyMessage:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeMessage:
    def __init__(self, fail_html: bool = False) -> None:
        self.fail_html = fail_html
        self.edit_calls: list[dict] = []
        self.reply_calls: list[dict] = []

    async def edit_text(self, text: str, **kwargs):  # type: ignore[no-untyped-def]
        self.edit_calls.append({"text": text, **kwargs})
        if kwargs.get("parse_mode") == "HTML" and self.fail_html:
            raise BadRequest("Can't parse entities")
        return self

    async def reply_text(self, text: str, **kwargs):  # type: ignore[no-untyped-def]
        self.reply_calls.append({"text": text, **kwargs})
        if kwargs.get("parse_mode") == "HTML" and self.fail_html:
            raise BadRequest("Can't parse entities")
        return FakeReplyMessage(text)


class BlockingMessage(FakeMessage):
    def __init__(self) -> None:
        super().__init__()
        self.release_edit = asyncio.Event()

    async def edit_text(self, text: str, **kwargs):  # type: ignore[no-untyped-def]
        self.edit_calls.append({"text": text, **kwargs})
        await self.release_edit.wait()
        return self


class TelegramStreamerTests(unittest.IsolatedAsyncioTestCase):
    async def test_message_update_does_not_block_on_telegram_edit(self) -> None:
        message = BlockingMessage()
        streamer = TelegramStreamer(message)

        await asyncio.wait_for(
            streamer.on_event("message_update", delta="hello"), timeout=0.05
        )
        await asyncio.sleep(0)

        self.assertEqual(streamer.buffer, "hello")
        self.assertEqual(len(message.edit_calls), 1)

        message.release_edit.set()
        await streamer.finalize()

    async def test_flush_renders_html(self) -> None:
        message = FakeMessage()
        streamer = TelegramStreamer(message)

        streamer.buffer = "**bold**\n```py\nprint('hi')\n```"
        await streamer.finalize()

        self.assertTrue(message.edit_calls)
        first = message.edit_calls[0]
        self.assertEqual(first["parse_mode"], "HTML")
        self.assertIn("<b>bold</b>", first["text"])
        self.assertIn("<pre><code>", first["text"])

    async def test_parse_error_falls_back_to_plain_text(self) -> None:
        message = FakeMessage(fail_html=True)
        streamer = TelegramStreamer(message)

        streamer.buffer = "plain < unsafe"
        await streamer.finalize()

        self.assertEqual(len(message.edit_calls), 2)
        self.assertEqual(message.edit_calls[0]["parse_mode"], "HTML")
        self.assertNotIn("parse_mode", message.edit_calls[1])
        self.assertEqual(message.edit_calls[1]["text"], "plain < unsafe")


if __name__ == "__main__":
    unittest.main()
