"""Ollama client. Every request goes to the 'tutor' model, which is pinned fully on the GPU."""

import json
from collections.abc import AsyncIterator

import httpx


class ModelError(RuntimeError):
    pass


def _explain(detail: str, model: str) -> str:
    text = detail.lower()
    if "not found" in text and "model" in text:
        return f"Model '{model}' is not installed. Run scripts\\start.ps1 once to create it."
    if any(k in text for k in ("out of memory", "cudamalloc", "insufficient", "requires more", "unable to allocate")):
        return ("The model does not fit in free GPU memory, so it was not loaded (it never falls back to RAM). "
                "Close apps that use the GPU, such as games or browsers with hardware acceleration, then try again.")
    return f"Model error: {detail.strip()[:300]}"


class LLM:
    def __init__(self, base_url: str, model: str, num_ctx: int):
        self.model = model
        self.num_ctx = num_ctx
        self.client = httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(600, connect=5))

    async def close(self) -> None:
        await self.client.aclose()

    async def stream_chat(self, messages: list[dict], think: bool = False) -> AsyncIterator[str]:
        """Stream reply text (the thinking itself is not returned). Thinking is off by default: conversation
        replies must start fast."""
        body = {"model": self.model, "messages": messages, "stream": True, "think": think,
                "options": {"num_ctx": self.num_ctx}}
        try:
            async with self.client.stream("POST", "/api/chat", json=body) as r:
                if r.status_code != 200:
                    raise ModelError(_explain((await r.aread()).decode(errors="replace"), self.model))
                async for line in r.aiter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    if "error" in chunk:
                        raise ModelError(_explain(chunk["error"], self.model))
                    text = chunk.get("message", {}).get("content")
                    if text:
                        yield text
                    if chunk.get("done"):
                        return
        except httpx.ConnectError:
            raise ModelError("Ollama is not running. Start the app with scripts\\start.ps1.") from None
        except httpx.HTTPError as e:
            # A timeout or a dropped connection mid-reply: as a ModelError the caller can report it and end the turn
            raise ModelError(f"Ollama request failed: {str(e) or type(e).__name__}") from None

    async def json_chat(self, prompt: str, schema: dict, *, think: bool = False,
                        temperature: float = 0.2) -> dict:
        """One structured call: Ollama constrains the output to the JSON schema."""
        body = {"model": self.model, "messages": [{"role": "user", "content": prompt}], "stream": False,
                "think": think, "format": schema,
                "options": {"num_ctx": self.num_ctx, "temperature": temperature}}
        for _ in range(2):
            try:
                r = await self.client.post("/api/chat", json=body)
            except httpx.ConnectError:
                raise ModelError("Ollama is not running. Start the app with scripts\\start.ps1.") from None
            except httpx.HTTPError as e:
                raise ModelError(f"Ollama request failed: {str(e) or type(e).__name__}") from None
            if r.status_code != 200:
                raise ModelError(_explain(r.text, self.model))
            try:
                return json.loads(r.json()["message"]["content"])
            except (json.JSONDecodeError, KeyError):
                continue
        raise ModelError("The model returned invalid JSON twice")

    async def status(self) -> dict:
        """Is Ollama up, is the model installed, and where is it loaded (GPU share)?"""
        result = {"ollama": None, "model": self.model, "installed": False, "loaded": False, "gpu_percent": None}
        try:
            result["ollama"] = (await self.client.get("/api/version")).json().get("version")
            tags = (await self.client.get("/api/tags")).json().get("models", [])
            result["installed"] = any(m["name"].split(":")[0] == self.model for m in tags)
            for m in (await self.client.get("/api/ps")).json().get("models", []):
                if m["name"].split(":")[0] == self.model:
                    result["loaded"] = True
                    result["gpu_percent"] = round(100 * m["size_vram"] / m["size"], 1) if m["size"] else None
                    result["vram_gb"] = round(m["size_vram"] / 1e9, 2)
        except httpx.HTTPError:
            pass
        return result
