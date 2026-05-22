"""Transport plugins for hp-notify.

Each module exposes:
    async def send(url: str, content: str) -> tuple[int, str]

Returns (http_status, error_message). Status 200-299 = success.
"""
