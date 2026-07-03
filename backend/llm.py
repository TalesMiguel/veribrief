import os
import time
from dataclasses import dataclass
from openai import OpenAI, RateLimitError
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

_client_cache: dict[str, OpenAI] = {}


def _default_model_for(provider_name: str) -> str:
    # Lets the eval harness swap the generator model for a whole run (e.g. to
    # compare gemini-3.1-flash-lite vs gemini-2.5-flash vs gemini-2.5-pro)
    # without touching the PROVIDERS registry or passing a model through every
    # agent call.
    if provider_name == "gemini":
        override = os.getenv("GEMINI_MODEL_OVERRIDE")
        if override:
            return override
    return PROVIDERS[provider_name].default_model


def get_client(provider_name: str | None = None) -> tuple[OpenAI, str]:
    provider_name = provider_name or os.getenv("LLM_PROVIDER", "gemini")

    if provider_name in _client_cache:
        return _client_cache[provider_name], _default_model_for(provider_name)

    if provider_name not in PROVIDERS:
        raise ValueError(f"Unknown LLM provider: {provider_name}. Available: {list(PROVIDERS.keys())}")

    config = PROVIDERS[provider_name]
    api_key = os.getenv(config.api_key_env)

    if not api_key:
        raise ValueError(f"Missing API key for provider '{provider_name}': set {config.api_key_env}")

    _client_cache[provider_name] = OpenAI(api_key=api_key, base_url=config.base_url)

    return _client_cache[provider_name], _default_model_for(provider_name)


def call_llm(
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0,
    max_retries: int = 5,
    provider: str | None = None,
) -> str:
    """`provider` lets a caller pin a specific backend regardless of the
    LLM_PROVIDER env default — used by the judge agent to deliberately use a
    different model family than the generator agents, so the critic does not
    share the generator's blind spots."""
    client, default_model = get_client(provider)
    if model is None:
        model = default_model

    # Lets the eval harness override temperature for a whole experiment run
    # (e.g. to check whether the near-zero run-to-run variance observed at
    # temperature=0 is a property of greedy decoding rather than a bug),
    # without editing every agent's call_llm() call site.
    temp_override = os.getenv("LLM_TEMPERATURE_OVERRIDE")
    if temp_override is not None:
        temperature = float(temp_override)

    # Fixed pacing between requests, on top of the reactive backoff below.
    # Needed for --repeat multi-run experiments (many calls back to back
    # across a whole pipeline run) against free/low-tier rate limits that a
    # single pipeline call alone doesn't trigger. 0 by default (no behavior
    # change for normal single-run usage).
    delay = float(os.getenv("LLM_CALL_DELAY_SECONDS", "0"))
    if delay > 0:
        time.sleep(delay)

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
            )
            return response.choices[0].message.content
        except RateLimitError:
            if attempt == max_retries - 1:
                raise
            time.sleep(2 ** attempt + 5)  # 6s, 7s, 9s, 13s, ...

    raise RuntimeError("unreachable")
