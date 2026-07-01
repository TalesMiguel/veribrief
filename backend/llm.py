import os
from dataclasses import dataclass
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()


@dataclass
class LLMProviderConfig:
    api_key_env: str
    base_url: str | None
    default_model: str


PROVIDERS: dict[str, LLMProviderConfig] = {
    "openai": LLMProviderConfig(
        api_key_env="OPENAI_API_KEY",
        base_url=None,
        default_model="gpt-4o-mini",
    ),
    "gemini": LLMProviderConfig(
        api_key_env="GEMINI_API_KEY",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        # default_model="gemini-2.5-flash",
        # default_model="gemini-2.5-flash-lite",
        default_model="gemini-3.1-flash-lite",
        # default_model="gemini-3.5-flash",
    ),
}

_client_cache: OpenAI | None = None
_provider_cache: str | None = None


def get_client() -> tuple[OpenAI, str]:
    global _client_cache, _provider_cache

    provider_name = os.getenv("LLM_PROVIDER", "gemini")

    if _client_cache is not None and _provider_cache == provider_name:
        return _client_cache, PROVIDERS[provider_name].default_model

    if provider_name not in PROVIDERS:
        raise ValueError(f"Unknown LLM provider: {provider_name}. Available: {list(PROVIDERS.keys())}")

    config = PROVIDERS[provider_name]
    api_key = os.getenv(config.api_key_env)

    if not api_key:
        raise ValueError(f"Missing API key for provider '{provider_name}': set {config.api_key_env}")

    _client_cache = OpenAI(api_key=api_key, base_url=config.base_url)
    _provider_cache = provider_name

    return _client_cache, config.default_model


def call_llm(
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0,
) -> str:
    client, default_model = get_client()
    if model is None:
        model = default_model

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
    )
    return response.choices[0].message.content
