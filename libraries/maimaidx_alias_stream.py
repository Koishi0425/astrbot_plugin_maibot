from collections.abc import AsyncIterator


async def iter_sse(lines: AsyncIterator[str | bytes]) -> AsyncIterator[dict]:
    """Parse an SSE stream, including heartbeat comments and multiline data.

    Args:
        lines: UTF-8 bytes or decoded lines, optionally ending in CRLF or LF.

    Yields:
        Events with data, type, persistent ID and optional reconnect interval.
    """
    event = "message"
    data = []
    event_id = None
    retry = None
    first_line = True
    async for line in lines:
        if isinstance(line, bytes):
            line = line.decode("utf-8")
        line = line.rstrip("\r\n")
        if first_line:
            line = line.removeprefix("\ufeff")
            first_line = False
        if not line:
            if data:
                yield {
                    "event": event,
                    "data": "\n".join(data),
                    "id": event_id,
                    "retry": retry,
                }
            elif retry is not None or event_id is not None:
                yield {"event": "control", "data": "", "id": event_id, "retry": retry}
            event, data, retry = "message", [], None
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        if field == "event":
            event = value
        elif field == "data":
            data.append(value)
        elif field == "id" and "\0" not in value:
            event_id = value
        elif field == "retry" and value.isascii() and value.isdecimal():
            retry = int(value)
    # Incomplete events at disconnect are discarded; the server replays them.
