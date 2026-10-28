import asyncio
import json


async def post_with_disconnect(app, path: str, body: bytes, headers: list[tuple[bytes, bytes]], disconnect_after_sends: int):
    """Drives the ASGI app directly so a real `http.disconnect` event reaches
    Starlette's StreamingResponse (an httpx test client never sends one, even
    if you stop reading the response early -- confirmed by spiking it), then
    reports how many body chunks were sent before the simulated disconnect.
    """
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [(b"content-type", b"application/json"), *headers],
        "query_string": b"",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
    }

    request_sent = False
    disconnect_event = asyncio.Event()
    status_code = None
    chunks: list[bytes] = []

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        await disconnect_event.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        nonlocal status_code
        if message["type"] == "http.response.start":
            status_code = message["status"]
        elif message["type"] == "http.response.body":
            chunk = message.get("body") or b""
            if chunk:
                chunks.append(chunk)
                if len(chunks) >= disconnect_after_sends:
                    disconnect_event.set()

    await app(scope, receive, send)
    return status_code, chunks


def json_body(payload: dict) -> bytes:
    return json.dumps(payload).encode()
