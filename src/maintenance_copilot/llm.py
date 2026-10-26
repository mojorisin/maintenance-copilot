"""Thin provider abstraction: one function returns a tool-calling chat model.

Switch providers with environment variables, no code changes:

    LLM_PROVIDER=anthropic   (default; LLM_MODEL defaults to claude-opus-5-5)
    LLM_PROVIDER=openai      LLM_MODEL=<model id>   needs: uv sync --extra openai
    LLM_PROVIDER=google      LLM_MODEL=<model id>   needs: uv sync --extra google
    LLM_PROVIDER=openrouter  LLM_MODEL=<vendor/model>  needs: uv sync --extra openai

Only Claude has a default model: it is the provider this project is
evaluated on. Defaults for other providers would go stale silently, so they
must be named explicitly.
"""

import os

from langchain_core.language_models import BaseChatModel

from maintenance_copilot import config

DEFAULT_ANTHROPIC_MODEL = "claude-opus-5-5"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Models that accept server-side refusal fallbacks: if a safety classifier
# declines a request, the API retries it on a fallback model automatically.
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}


class ProviderError(RuntimeError):
    """The selected provider is unknown, unconfigured or not installed."""


def _require_key(var: str) -> str:
    key = os.getenv(var)
    if not key:
        raise ProviderError(f"{var} is not set. Add it to .env (see .env.example).")
    return key


def _require_model(provider: str, model: str | None) -> str:
    if not model:
        raise ProviderError(f"Set LLM_MODEL to a {provider} model ID in .env.")
    return model


def _install_hint(extra: str) -> ProviderError:
    return ProviderError(f"Provider package missing. Install it with: uv sync --extra {extra}")


def get_chat_model(provider: str | None = None, model: str | None = None) -> BaseChatModel:
    """Build the chat model for `provider` (default: LLM_PROVIDER)."""
    provider = (provider or config.LLM_PROVIDER).lower()
    model = model or config.LLM_MODEL

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        model = model or DEFAULT_ANTHROPIC_MODEL
        # Automatic prompt caching: the system prompt, tool definitions and
        # earlier turns are re-sent on every agent step, so later steps read
        # them from cache at a fraction of the input price.
        model_kwargs: dict = {"cache_control": {"type": "ephemeral"}}
        betas = []
        if model in FALLBACK_MODELS:
            betas.append("server-side-fallback-2026-07-01")
            model_kwargs["fallbacks"] = "default"
        return ChatAnthropic(
            model=model,
            api_key=_require_key("ANTHROPIC_API_KEY"),
            max_tokens=16_000,
            effort=config.LLM_EFFORT,
            max_retries=3,
            betas=betas or None,
            model_kwargs=model_kwargs,
        )

    # Other providers: validate configuration first, then import the package,
    # so every config problem is reported before asking for an install.
    if provider in ("openai", "openrouter"):
        model = _require_model(provider, model)
        if provider == "openai":
            extra = {"api_key": _require_key("OPENAI_API_KEY")}
        else:
            extra = {"api_key": _require_key("OPENROUTER_API_KEY"), "base_url": OPENROUTER_BASE_URL}
        try:
            from langchain_openai import ChatOpenAI
        except ImportError:
            raise _install_hint("openai") from None
        return ChatOpenAI(model=model, **extra)

    if provider == "google":
        model = _require_model(provider, model)
        key = _require_key("GOOGLE_API_KEY")
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
        except ImportError:
            raise _install_hint("google") from None
        return ChatGoogleGenerativeAI(model=model, google_api_key=key)

    raise ProviderError(
        f"Unknown LLM_PROVIDER '{provider}'. Use anthropic, openai, google or openrouter."
    )


def model_name(llm: BaseChatModel) -> str:
    """The model ID a chat model was configured with (for logs and pricing)."""
    return getattr(llm, "model", None) or getattr(llm, "model_name", None) or "unknown"
