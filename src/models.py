from langchain.chat_models import init_chat_model

from src.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL_NAME


def build_chat_model():
    return init_chat_model(
        model=LLM_MODEL_NAME,
        model_provider="openai",
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL,
    )