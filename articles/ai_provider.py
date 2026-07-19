"""
AI Provider 模块 - 配置驱动的 AI 调用架构

支持多种 AI Provider，通过环境变量或配置切换：
- openai: OpenAI API
- anthropic: Anthropic Claude API
- google: Google Gemini API
- local: 本地模型（Ollama 等）
- trae: Trae SDK（仅在显式设置 AI_PROVIDER=trae 时生效）
- mock: 模拟调用（仅测试用）

A4 增强：
- 重试 + 指数退避：限流/瞬错自动重试，最终失败才降级。
- token 用量元数据：summarize_with_meta 返回 {content, usage, model}，usage 写入笔记 frontmatter。

降级说明：
- 自动检测时仅检测外部 Provider（openai/anthropic/google/local）。
- Trae SDK 不作为自动检测项，归入降级流程由外层对话接手处理。
- 显式设置 AI_PROVIDER=trae 时，TraeProvider 可被获取。
"""

import os
import time
import logging
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 重试 / 退避（A4）
# ---------------------------------------------------------------------------

def _is_retryable(exc: Exception) -> bool:
    """判断异常是否值得重试（限流 / 超时 / 连接抖动 / 5xx 等瞬错）。"""
    msg = str(exc).lower()
    keywords = [
        "429", "rate", "ratelimit", "too many requests",
        "timeout", "timed out", "deadline",
        "connection", "connreset", "broken pipe", "reset by peer",
        "503", "502", "500", "524", "504",
        "overloaded", "try again", "temporarily", "unavailable",
        "econnreset", "sslerror", "socket", "temporary failure",
    ]
    return any(k in msg for k in keywords)


def with_retry(func, max_retries: int = 3, base_delay: float = 2.0, backoff: float = 2.0):
    """对 func 执行指数退避重试；可重试异常才重试，最终失败向上抛出。"""
    max_retries = max(1, int(max_retries))
    last_exc = None
    for attempt in range(max_retries):
        try:
            return func()
        except Exception as e:
            last_exc = e
            if not _is_retryable(e):
                # 不可重试（如鉴权失败）→ 直接抛出，由上层转降级
                raise
            if attempt < max_retries - 1:
                delay = base_delay * (backoff ** attempt)
                logger.warning(f"[retry] 第 {attempt+1} 次调用失败（{type(e).__name__}），{delay:.1f}s 后重试: {e}")
                time.sleep(delay)
    # 重试耗尽
    raise last_exc


def _extract_usage(resp, name: str) -> Optional[Dict[str, int]]:
    """从各 SDK 响应中提取 token 用量（兼容不同字段名）。"""
    try:
        if name in ("openai", "local"):
            u = getattr(resp, "usage", None)
            if u:
                pt = getattr(u, "prompt_tokens", None) or getattr(u, "input_tokens", None)
                ct = getattr(u, "completion_tokens", None) or getattr(u, "output_tokens", None)
                tot = getattr(u, "total_tokens", None)
                if pt is not None or ct is not None:
                    return {"prompt_tokens": int(pt or 0), "completion_tokens": int(ct or 0),
                            "total_tokens": int(tot or (pt or 0) + (ct or 0))}
        elif name == "anthropic":
            u = getattr(resp, "usage", None)
            if u:
                ipt = getattr(u, "input_tokens", None)
                out = getattr(u, "output_tokens", None)
                if ipt is not None or out is not None:
                    return {"prompt_tokens": int(ipt or 0), "completion_tokens": int(out or 0),
                            "total_tokens": int(ipt or 0) + int(out or 0)}
        elif name == "google":
            um = getattr(resp, "usage_metadata", None)
            if um:
                pt = getattr(um, "prompt_token_count", None)
                ct = getattr(um, "candidates_token_count", None)
                tot = getattr(um, "total_token_count", None)
                if pt is not None or ct is not None:
                    return {"prompt_tokens": int(pt or 0), "completion_tokens": int(ct or 0),
                            "total_tokens": int(tot or (pt or 0) + (ct or 0))}
    except Exception:
        return None
    return None


class AIProviderBase(ABC):
    """AI Provider 抽象基类"""

    name: str = "base"

    @abstractmethod
    def is_available(self) -> bool:
        pass

    @abstractmethod
    def summarize(self, prompt: str, content: str, **kwargs) -> Optional[str]:
        pass

    def summarize_with_meta(self, prompt: str, content: str, **kwargs) -> Optional[Dict[str, Any]]:
        """带元数据的总结：返回 {content, usage, model} 或抛出异常。

        默认实现：调用 summarize 并包装成 meta。子类可重写以捕获真实 usage。
        """
        text = self.summarize(prompt, content, **kwargs)
        if text is None:
            raise RuntimeError(f"{self.name} 返回空结果")
        return {"content": text, "usage": None, "model": self.name}

    def _build_full_prompt(self, prompt: str, content: str) -> str:
        return f"{prompt}\n\n---\n\n请根据以上规则，总结以下文章内容：\n\n{content}"


class TraeProvider(AIProviderBase):
    name = "trae"

    def is_available(self) -> bool:
        try:
            from trae import llm
            return True
        except ImportError:
            return False

    def summarize(self, prompt: str, content: str, **kwargs) -> Optional[str]:
        if not self.is_available():
            logger.error("Trae SDK 不可用")
            return None
        try:
            from trae import llm
            full_prompt = self._build_full_prompt(prompt, content)
            temperature = kwargs.get('temperature', 0.7)
            print("   🎯 开启 Trae 临时子会话进行总结...")
            with llm.create_session() as session:
                response = session.chat(
                    messages=[{"role": "user", "content": full_prompt}],
                    temperature=temperature
                )
            print("   🎯 Trae 临时子会话总结完成")
            return response.content.strip()
        except Exception as e:
            logger.error(f"Trae 调用失败: {str(e)}")
            return None


class OpenAIProvider(AIProviderBase):
    name = "openai"

    def is_available(self) -> bool:
        api_key = os.getenv("OPENAI_API_KEY")
        return bool(api_key)

    def _model(self):
        return os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    def summarize(self, prompt: str, content: str, **kwargs) -> Optional[str]:
        try:
            return self.summarize_with_meta(prompt, content, **kwargs)["content"]
        except Exception:
            return None

    def summarize_with_meta(self, prompt: str, content: str, **kwargs) -> Dict[str, Any]:
        if not self.is_available():
            raise RuntimeError("OpenAI API Key 未配置")
        import openai
        full_prompt = self._build_full_prompt(prompt, content)
        model = self._model()
        temperature = kwargs.get('temperature', 0.7)
        print(f"   🎯 使用 OpenAI API ({model}) 进行总结...")
        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        response = with_retry(lambda: client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": full_prompt}],
            temperature=temperature,
        ))
        print("   🎯 OpenAI 总结完成")
        usage = _extract_usage(response, "openai")
        return {"content": response.choices[0].message.content.strip(), "usage": usage, "model": model}


class AnthropicProvider(AIProviderBase):
    name = "anthropic"

    def is_available(self) -> bool:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        return bool(api_key)

    def _model(self):
        return os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")

    def summarize(self, prompt: str, content: str, **kwargs) -> Optional[str]:
        try:
            return self.summarize_with_meta(prompt, content, **kwargs)["content"]
        except Exception:
            return None

    def summarize_with_meta(self, prompt: str, content: str, **kwargs) -> Dict[str, Any]:
        if not self.is_available():
            raise RuntimeError("Anthropic API Key 未配置")
        import anthropic
        full_prompt = self._build_full_prompt(prompt, content)
        model = self._model()
        temperature = kwargs.get('temperature', 0.7)
        print(f"   🎯 使用 Anthropic Claude API ({model}) 进行总结...")
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        response = with_retry(lambda: client.messages.create(
            model=model,
            max_tokens=kwargs.get("max_tokens", 4096),
            temperature=temperature,
            messages=[{"role": "user", "content": full_prompt}]
        ))
        print("   🎯 Anthropic Claude 总结完成")
        usage = _extract_usage(response, "anthropic")
        return {"content": response.content[0].text.strip(), "usage": usage, "model": model}


class GoogleProvider(AIProviderBase):
    name = "google"

    def is_available(self) -> bool:
        api_key = os.getenv("GOOGLE_API_KEY")
        return bool(api_key)

    def _model(self):
        return os.getenv("GOOGLE_MODEL", "gemini-2.0-flash")

    def summarize(self, prompt: str, content: str, **kwargs) -> Optional[str]:
        try:
            return self.summarize_with_meta(prompt, content, **kwargs)["content"]
        except Exception:
            return None

    def summarize_with_meta(self, prompt: str, content: str, **kwargs) -> Dict[str, Any]:
        if not self.is_available():
            raise RuntimeError("Google API Key 未配置")
        import google.genai as genai
        full_prompt = self._build_full_prompt(prompt, content)
        model = self._model()
        temperature = kwargs.get('temperature', 0.7)
        print(f"   🎯 使用 Google Gemini API ({model}) 进行总结...")
        genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
        client = genai.Client()
        response = with_retry(lambda: client.models.generate_content(
            model=model,
            contents=full_prompt,
            config=genai.types.GenerateContentConfig(temperature=temperature)
        ))
        print("   🎯 Google Gemini 总结完成")
        usage = _extract_usage(response, "google")
        return {"content": response.text.strip(), "usage": usage, "model": model}


class LocalProvider(AIProviderBase):
    name = "local"

    def is_available(self) -> bool:
        api_base = os.getenv("LOCAL_API_BASE")
        return bool(api_base)

    def _model(self):
        return os.getenv("LOCAL_MODEL", "llama3")

    def summarize(self, prompt: str, content: str, **kwargs) -> Optional[str]:
        try:
            return self.summarize_with_meta(prompt, content, **kwargs)["content"]
        except Exception:
            return None

    def summarize_with_meta(self, prompt: str, content: str, **kwargs) -> Dict[str, Any]:
        if not self.is_available():
            raise RuntimeError("本地模型 API 地址未配置 (LOCAL_API_BASE)")
        import openai
        full_prompt = self._build_full_prompt(prompt, content)
        model = self._model()
        temperature = kwargs.get('temperature', 0.7)
        api_base = os.getenv("LOCAL_API_BASE", "http://localhost:11434/v1")
        print(f"   🎯 使用本地模型 ({model}) 进行总结...")
        client = openai.OpenAI(api_key="ollama", base_url=api_base)
        response = with_retry(lambda: client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": full_prompt}],
            temperature=temperature,
        ))
        print("   🎯 本地模型总结完成")
        usage = _extract_usage(response, "local")
        return {"content": response.choices[0].message.content.strip(), "usage": usage, "model": model}


class MockProvider(AIProviderBase):
    name = "mock"

    def is_available(self) -> bool:
        return os.getenv("AI_PROVIDER", "") == "mock"

    def summarize(self, prompt: str, content: str, **kwargs) -> Optional[str]:
        try:
            return self.summarize_with_meta(prompt, content, **kwargs)["content"]
        except Exception:
            return None

    def summarize_with_meta(self, prompt: str, content: str, **kwargs) -> Dict[str, Any]:
        print("   🎯 [Mock Provider] 模拟 AI 总结...")
        mock_result = (
            "# 一、文章总结\n\n"
            "这是由 Mock Provider 生成的模拟总结结果。\n\n"
            "## 1. 概要\n\n"
            f"- 内容长度：{len(content)} 字符\n"
            "- 标签：测试标签\n\n"
            "## 2. 核心内容\n\n"
            "- 第一个要点\n- 第二个要点\n- 第三个要点\n\n"
            "## 3. 总结\n\n本文已通过 Mock Provider 完成模拟总结。\n"
        )
        print("   🎯 [Mock Provider] 模拟总结完成")
        # 模拟 token 用量，便于验证 A4 frontmatter
        return {
            "content": mock_result,
            "usage": {"prompt_tokens": 120, "completion_tokens": 80, "total_tokens": 200},
            "model": "mock-model",
        }


class AIProviderManager:
    """AI Provider 管理器"""

    EXTERNAL_PROVIDERS = ["openai", "anthropic", "google", "local", "mock"]

    def __init__(self):
        self._providers = {}
        self._init_providers()

    def _init_providers(self):
        self._providers = {
            "trae": TraeProvider(),
            "openai": OpenAIProvider(),
            "anthropic": AnthropicProvider(),
            "google": GoogleProvider(),
            "local": LocalProvider(),
            "mock": MockProvider(),
        }

    def get_provider(self, provider_name: str = None) -> Optional[AIProviderBase]:
        if provider_name:
            return self._providers.get(provider_name.lower())
        configured = os.getenv("AI_PROVIDER", "").lower()
        if configured and configured in self._providers:
            provider = self._providers[configured]
            if provider.is_available():
                return provider
        priority = ["openai", "anthropic", "google", "local"]
        for name in priority:
            provider = self._providers.get(name)
            if provider and provider.is_available():
                print(f"   📋 自动选择 AI Provider: {name}")
                return provider
        return None

    def get_external_provider(self) -> Optional[AIProviderBase]:
        configured = os.getenv("AI_PROVIDER", "").lower()
        if configured and configured in self.EXTERNAL_PROVIDERS:
            provider = self._providers.get(configured)
            if provider and provider.is_available():
                print(f"   📋 使用配置的外部 AI Provider: {configured}")
                return provider
        for name in self.EXTERNAL_PROVIDERS:
            provider = self._providers.get(name)
            if provider and provider.is_available():
                print(f"   📋 自动选择外部 AI Provider: {name}")
                return provider
        return None

    def has_external_provider(self) -> bool:
        return self.get_external_provider() is not None

    def get_available_providers(self) -> list:
        return [name for name, p in self._providers.items() if p.is_available()]

    def get_external_available_providers(self) -> list:
        return [name for name in self.EXTERNAL_PROVIDERS if self._providers.get(name) and self._providers[name].is_available()]

    def list_all_providers(self) -> dict:
        return {
            name: {"available": p.is_available(), "name": p.name, "is_external": name in self.EXTERNAL_PROVIDERS}
            for name, p in self._providers.items()
        }


_provider_manager = AIProviderManager()


def get_ai_provider(provider_name: str = None) -> Optional[AIProviderBase]:
    return _provider_manager.get_provider(provider_name)


def get_external_ai_provider() -> Optional[AIProviderBase]:
    return _provider_manager.get_external_provider()


def has_external_provider() -> bool:
    return _provider_manager.has_external_provider()


def list_available_providers() -> list:
    return _provider_manager.get_available_providers()


def list_external_providers() -> list:
    return _provider_manager.get_external_available_providers()


def call_ai_summarize(prompt: str, content: str, provider_name: str = None, **kwargs) -> Optional[str]:
    """调用 AI 进行总结（返回纯文本）。"""
    provider = get_ai_provider(provider_name)
    if provider is None:
        logger.error("没有可用的 AI Provider")
        return None
    try:
        meta = provider.summarize_with_meta(prompt, content, **kwargs)
        return meta.get("content") if meta else None
    except Exception as e:
        logger.error(f"AI 总结失败: {e}")
        return None


def call_external_ai_summarize(prompt: str, content: str, **kwargs) -> Optional[str]:
    """调用外部 AI Provider（排除 Trae），返回纯文本；无配置返回 None。"""
    meta = call_external_ai_summarize_meta(prompt, content, **kwargs)
    return meta.get("content") if meta else None


def call_external_ai_summarize_meta(prompt: str, content: str, **kwargs) -> Optional[Dict[str, Any]]:
    """调用外部 AI Provider（排除 Trae），返回带 usage 的 meta dict。

    带重试/退避（A4）。最终失败返回 None（触发降级），绝不抛异常到主流程。
    """
    provider = get_external_ai_provider()
    if provider is None:
        logger.info("没有可用的外部 AI Provider，将触发降级流程")
        return None
    try:
        meta = provider.summarize_with_meta(prompt, content, **kwargs)
        if meta and meta.get("content"):
            return meta
        return None
    except Exception as e:
        logger.error(f"外部 AI Provider 调用失败: {e}")
        return None
