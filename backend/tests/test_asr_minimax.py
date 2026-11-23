import asyncio

import httpx
import pytest

from app.config import Settings
from app.providers.asr_minimax import MiniMaxASR


def collect(handler):
    async def run():
        cfg = Settings(minimax_api_key="test-token", minimax_asr_language="")
        asr = MiniMaxASR(cfg)
        await asr.close()
        asr._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return [result async for result in asr.transcribe_stream(bytes(3200), language="en")]
        finally:
            await asr.close()

    return asyncio.run(run())


def test_sse_transcript_accumulates_deltas_and_keeps_word_spacing():
    def handler(request):
        assert request.headers["language"] == "en"
        assert b'name="stream"\r\n\r\ntrue\r\n' in request.read()
        return httpx.Response(
            200, headers={"Content-Type": "text/event-stream"},
            text=(
                ': keepalive\n\n'
                'data: {"delta":"Read","finish":false}\n\n'
                'data: {"delta":" this page","finish":false}\n\n'
                'data: {"delta":".","finish":true,"duration":1.2}\n\n'
            ),
        )

    results = collect(handler)
    assert [r.text for r in results] == ["Read", "Read this page", "Read this page."]
    assert [r.partial for r in results] == [True, True, False]
    assert results[-1].duration_ms == 1200


def test_plain_json_response_still_delivers_one_final_transcript():
    results = collect(lambda request: httpx.Response(200, json={"text": "Read this page", "duration": 1}))
    assert len(results) == 1
    assert not results[0].partial
    assert results[0].text == "Read this page"


def test_truncated_sse_is_not_promoted_to_final():
    def handler(request):
        return httpx.Response(
            200, headers={"Content-Type": "text/event-stream"},
            text='data: {"delta":"Read","finish":false}\n\n',
        )

    with pytest.raises(RuntimeError, match="without a final transcription"):
        collect(handler)
