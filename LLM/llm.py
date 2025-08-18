import os

from langchain_openai import ChatOpenAI
from LLM.config import get_config_value


base_url = get_config_value("llm.base_url", "OLLAMA_BASE_URL", "http://localhost:11434/v1", str)
api_key = get_config_value("llm.api_key", "OLLAMA_API_KEY", "ollama", str)
model_name = get_config_value("llm.model", "OLLAMA_MODEL", "gpt-oss:20b", str)
# model_name = os.getenv("OLLAMA_MODEL", "gemma3:4b")


def create_chat_model(temperature: float = 0) -> ChatOpenAI:
    # Request timeout in seconds for each LLM call (avoid hangs)
    request_timeout = get_config_value("llm.request_timeout_s", "LLM_REQUEST_TIMEOUT", 30.0, float)
    # Max retries kept low to avoid long stalls
    max_retries = get_config_value("llm.max_retries", "LLM_REQUEST_MAX_RETRIES", 0, int)
    return ChatOpenAI(
        model=model_name,
        base_url=base_url,
        api_key=api_key,
        temperature=temperature,
        timeout=request_timeout,
        max_retries=max_retries,
    )


