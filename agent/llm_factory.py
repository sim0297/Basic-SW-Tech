from langchain_core.language_models import BaseChatModel

from config.settings import settings


def get_llm(
    provider: str,
    model: str,
    temperature: float = 0.1,
    **kwargs,
) -> BaseChatModel:
    if provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            base_url=settings.OLLAMA_BASE_URL,
            model=model,
            temperature=temperature,
            **kwargs,
        )
    elif provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model,
            api_key=settings.OPENAI_API_KEY or None,
            temperature=temperature,
            **kwargs,
        )
    elif provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=model,
            api_key=settings.ANTHROPIC_API_KEY or None,
            temperature=temperature,
            **kwargs,
        )
    elif provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=model,
            google_api_key=settings.GOOGLE_API_KEY or None,
            temperature=temperature,
            **kwargs,
        )
    else:
        raise ValueError(f"지원하지 않는 프로바이더: {provider}")
