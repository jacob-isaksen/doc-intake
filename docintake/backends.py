"""LLM backends.

Everything the rest of the program knows about a model is this one method:

    complete_json(system, user, schema) -> str   (a JSON string)

Each class below is an adapter that hides one provider behind that method, so
swapping providers means picking a different class, not rewriting the pipeline.
"""
import json
import os
import urllib.request
from typing import Protocol


class Backend(Protocol):
    name: str

    def complete_json(self, system: str, user: str, schema: dict) -> str: ...


class OllamaBackend:
    """Runs an open model on this machine through Ollama. Free, nothing leaves the PC."""

    def __init__(self, model: str = "qwen2.5:7b"):
        self.model = model
        self.name = f"ollama:{model}"

    def complete_json(self, system: str, user: str, schema: dict) -> str:
        import ollama

        response = ollama.chat(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            format=schema,  # Ollama constrains the output to this JSON schema
            options={"temperature": 0},
        )
        return response.message.content


class GeminiBackend:
    """Google Gemini over plain HTTPS. Needs GEMINI_API_KEY in the environment.

    Not yet run live: written against Google's REST docs, untested until a key exists.
    """

    def __init__(self, model: str = "gemini-2.5-flash"):
        self.model = model
        self.name = f"gemini:{model}"

    def complete_json(self, system: str, user: str, schema: dict) -> str:
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent"
        )
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
        }
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "x-goog-api-key": key},
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            data = json.load(response)
        return data["candidates"][0]["content"]["parts"][0]["text"]
