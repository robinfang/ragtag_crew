"""
探测 GLM streaming 的原始字节行为。

目标：观察 content 结束到 tool_call 开始之间，服务端是否有字节活动
（keepalive、空 SSE 行、注释等），或者是完全静默。

用法：
    uv run python scripts/probe_glm_stream.py
"""

import asyncio
import os
import time

import aiohttp
from dotenv import load_dotenv

load_dotenv()

API_BASE = os.getenv("GLM_API_BASE", "https://open.bigmodel.cn/api/coding/paas/v4")
API_KEY = os.getenv("GLM_API_KEY", "")
MODEL = "GLM-5-Turbo"

# 一个简单的会触发 tool call 的 prompt
MESSAGES = [
    {
        "role": "system",
        "content": (
            "You are a helpful coding assistant. "
            "Before calling any tool, first output a brief explanation of what you are about to do."
        ),
    },
    {
        "role": "user",
        "content": "请用 write 工具写一个 hello.txt，内容是 hello world。",
    },
]

# 会触发 tool call 的最简 write 工具定义
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "write",
            "description": "Write content to a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    }
]

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}

PAYLOAD = {
    "model": MODEL,
    "messages": MESSAGES,
    "tools": TOOLS,
    "stream": True,
}


async def main() -> None:
    print(f"连接 {API_BASE}/chat/completions")
    print(f"Model: {MODEL}")
    print("-" * 60)

    t0 = time.monotonic()

    phase = "start"  # start → reasoning → content → tool_call → done
    last_byte_t = t0
    last_token_t = t0
    byte_count = 0
    line_count = 0
    gap_log: list[tuple[str, float]] = []  # (事件, 距上次字节时间)

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{API_BASE}/chat/completions",
            headers=HEADERS,
            json=PAYLOAD,
            timeout=aiohttp.ClientTimeout(total=300, sock_read=0),  # 禁用 sock_read 超时
        ) as resp:
            print(f"HTTP {resp.status}  Content-Type: {resp.headers.get('content-type', '?')}")
            print("-" * 60)

            async for raw_chunk in resp.content.iter_chunked(1024):
                now = time.monotonic()
                gap = now - last_byte_t
                byte_count += len(raw_chunk)
                last_byte_t = now

                # 拆成行，逐行分析
                try:
                    text = raw_chunk.decode("utf-8", errors="replace")
                except Exception:
                    text = repr(raw_chunk)

                for line in text.split("\n"):
                    line = line.rstrip("\r")
                    line_count += 1

                    if not line:
                        # 空行：SSE 事件分隔符
                        continue

                    if line.startswith(":"):
                        # SSE 注释 / keepalive
                        elapsed = now - t0
                        gap_since_last_token = now - last_token_t
                        print(
                            f"[{elapsed:7.2f}s] KEEPALIVE  gap_from_last_token={gap_since_last_token:.2f}s  raw={line!r}"
                        )
                        gap_log.append(("keepalive", gap_since_last_token))
                        continue

                    if line.startswith("data:"):
                        data = line[5:].strip()
                        elapsed = now - t0
                        gap_since_last_token = now - last_token_t

                        if data == "[DONE]":
                            print(f"[{elapsed:7.2f}s] DONE")
                            phase = "done"
                            break

                        import json
                        try:
                            obj = json.loads(data)
                        except json.JSONDecodeError:
                            print(f"[{elapsed:7.2f}s] BAD JSON: {data[:80]!r}")
                            continue

                        choices = obj.get("choices", [])
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {})

                        # 分析 delta 类型
                        reasoning = delta.get("reasoning_content")
                        content = delta.get("content")
                        tool_calls = delta.get("tool_calls")

                        if reasoning:
                            if phase == "start":
                                phase = "reasoning"
                                print(f"[{elapsed:7.2f}s] ── reasoning 开始 ──")
                            last_token_t = now

                        elif content:
                            if phase in ("start", "reasoning"):
                                prev_phase = phase
                                phase = "content"
                                print(
                                    f"[{elapsed:7.2f}s] ── content 开始 ──"
                                    + (f"（reasoning 结束，gap={gap_since_last_token:.2f}s）" if prev_phase == "reasoning" else "")
                                )
                            last_token_t = now
                            # 只打印第一个 content token
                            if gap_since_last_token > 0.5:
                                print(f"[{elapsed:7.2f}s] content  gap={gap_since_last_token:.2f}s  token={content!r}")

                        elif tool_calls:
                            if phase != "tool_call":
                                gap_since_last_token = now - last_token_t
                                print(
                                    f"[{elapsed:7.2f}s] ── tool_call 开始 ──  gap_from_last_token={gap_since_last_token:.2f}s"
                                )
                                gap_log.append(("content→tool_call gap", gap_since_last_token))
                                phase = "tool_call"
                            last_token_t = now

                        elif delta.get("role") and not reasoning and not content and not tool_calls:
                            # 空 delta（role only），通常是第一个 chunk
                            pass

    print("-" * 60)
    print(f"总字节: {byte_count}  总行: {line_count}  耗时: {time.monotonic() - t0:.2f}s")
    if gap_log:
        print("\n关键 gap 汇总:")
        for label, gap in gap_log:
            print(f"  {label}: {gap:.2f}s")


if __name__ == "__main__":
    asyncio.run(main())
