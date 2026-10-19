"""The only module that knows about Ollama. Everything else takes an `llm` callable."""
from openai import OpenAI

MODEL = "llama3:8b"
_client = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama", timeout=60)


def chat(messages: list[dict], json_mode: bool = False) -> str:
    kwargs = {"response_format": {"type": "json_object"}} if json_mode else {}
    resp = _client.chat.completions.create(model=MODEL, messages=messages, temperature=0, **kwargs)
    return resp.choices[0].message.content or ""
