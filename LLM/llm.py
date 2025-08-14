import os

from langchain_openai import ChatOpenAI


base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
api_key = os.getenv("OLLAMA_API_KEY", "ollama")
model_name = os.getenv("OLLAMA_MODEL", "gpt-oss:20b")


def create_chat_model(temperature: float = 0) -> ChatOpenAI:
    return ChatOpenAI(
        model=model_name,
        base_url=base_url,
        api_key=api_key,
        temperature=temperature,
    )


