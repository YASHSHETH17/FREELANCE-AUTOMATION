from langchain.chat_models import init_chat_model

from src.config import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MAX_COMPLETION_TOKENS,
    LLM_MODEL_NAME,
    LLM_REASONING_EFFORT,
    LLM_TIMEOUT_SECONDS,
)


def build_chat_model():
    model_options = {
        "api_key": LLM_API_KEY or "ollama",
        "base_url": LLM_BASE_URL,
        "max_completion_tokens": LLM_MAX_COMPLETION_TOKENS,
        "timeout": LLM_TIMEOUT_SECONDS,
        "max_retries": 0,
    }
    if LLM_REASONING_EFFORT:
        model_options["reasoning_effort"] = LLM_REASONING_EFFORT

    return init_chat_model(
        model=LLM_MODEL_NAME,
        model_provider="openai",
        **model_options,
    )
