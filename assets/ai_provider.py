"""
AI Provider 模块 - 配置驱动的 AI 调用架构

支持多种 AI Provider，通过环境变量或配置切换：
- openai: OpenAI API
- anthropic: Anthropic Claude API
- google: Google Gemini API
- local: 本地模型（Ollama 等）
- trae: Trae SDK（仅在显式设置 AI_PROVIDER=trae 时生效）
- mock: 模拟调用（仅测试用）

使用方式：
1. 设置环境变量 AI_PROVIDER=xxx
2. 配置对应的 API Key 等参数
3. 调用 get_ai_provider() 获取对应 Provider 实例

降级说明：
- 自动检测时仅检测外部 Provider（openai/anthropic/google/local），需要显式 API Key
- Trae SDK 不作为自动检测项，归入降级流程由外层对话接手处理
- 显式设置 AI_PROVIDER=trae 时，TraeProvider 可被 get_provider() 获取
"""

import os
import logging
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger(__name__)


class AIProviderBase(ABC):
    """AI Provider 抽象基类
    
    所有 AI Provider 必须实现以下方法：
    - is_available(): 检查当前环境是否支持此 Provider
    - summarize(prompt, content): 使用 prompt 总结 content
    """
    
    name: str = "base"
    
    @abstractmethod
    def is_available(self) -> bool:
        """检查当前环境是否支持此 Provider"""
        pass
    
    @abstractmethod
    def summarize(self, prompt: str, content: str, **kwargs) -> Optional[str]:
        """使用 prompt 总结 content
        
        Args:
            prompt: 提示词模板
            content: 需要总结的文章内容
            **kwargs: 其他可选参数（如 temperature 等）
        
        Returns:
            str: 总结结果，失败返回 None
        """
        pass
    
    def _build_full_prompt(self, prompt: str, content: str) -> str:
        """构建完整的 AI 请求内容
        
        Args:
            prompt: 提示词模板
            content: 文章内容
        
        Returns:
            str: 拼接后的完整提示词
        """
        return f"{prompt}\n\n---\n\n请根据以上规则，总结以下文章内容：\n\n{content}"


class TraeProvider(AIProviderBase):
    """Trae SDK Provider
    
    使用 Trae SDK 的子对话功能进行 AI 调用。
    在同一模型进程内完成总结任务，避免额外 API 调用，保持上下文一致性。
    """
    
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
    """OpenAI API Provider
    
    使用 OpenAI API 进行 AI 调用，需要配置 OPENAI_API_KEY 环境变量。
    """
    
    name = "openai"
    
    def is_available(self) -> bool:
        api_key = os.getenv("OPENAI_API_KEY")
        return api_key is not None and api_key != ""
    
    def summarize(self, prompt: str, content: str, **kwargs) -> Optional[str]:
        if not self.is_available():
            logger.error("OpenAI API Key 未配置")
            return None
        
        try:
            import openai
            
            full_prompt = self._build_full_prompt(prompt, content)
            model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
            temperature = kwargs.get('temperature', 0.7)
            
            print(f"   🎯 使用 OpenAI API ({model}) 进行总结...")
            
            client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": full_prompt}],
                temperature=temperature
            )
            
            print("   🎯 OpenAI 总结完成")
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            logger.error(f"OpenAI API 调用失败: {str(e)}")
            return None


class AnthropicProvider(AIProviderBase):
    """Anthropic Claude API Provider
    
    使用 Anthropic Claude API 进行 AI 调用，需要配置 ANTHROPIC_API_KEY 环境变量。
    """
    
    name = "anthropic"
    
    def is_available(self) -> bool:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        return api_key is not None and api_key != ""
    
    def summarize(self, prompt: str, content: str, **kwargs) -> Optional[str]:
        if not self.is_available():
            logger.error("Anthropic API Key 未配置")
            return None
        
        try:
            import anthropic
            
            full_prompt = self._build_full_prompt(prompt, content)
            model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
            temperature = kwargs.get('temperature', 0.7)
            
            print(f"   🎯 使用 Anthropic Claude API ({model}) 进行总结...")
            
            client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
            response = client.messages.create(
                model=model,
                max_tokens=4096,
                temperature=temperature,
                messages=[{"role": "user", "content": full_prompt}]
            )
            
            print("   🎯 Anthropic Claude 总结完成")
            return response.content[0].text.strip()
            
        except Exception as e:
            logger.error(f"Anthropic API 调用失败: {str(e)}")
            return None


class GoogleProvider(AIProviderBase):
    """Google Gemini API Provider
    
    使用 Google Gemini API 进行 AI 调用，需要配置 GOOGLE_API_KEY 环境变量。
    """
    
    name = "google"
    
    def is_available(self) -> bool:
        api_key = os.getenv("GOOGLE_API_KEY")
        return api_key is not None and api_key != ""
    
    def summarize(self, prompt: str, content: str, **kwargs) -> Optional[str]:
        if not self.is_available():
            logger.error("Google API Key 未配置")
            return None
        
        try:
            import google.genai as genai
            
            full_prompt = self._build_full_prompt(prompt, content)
            model = os.getenv("GOOGLE_MODEL", "gemini-2.0-flash")
            temperature = kwargs.get('temperature', 0.7)
            
            print(f"   🎯 使用 Google Gemini API ({model}) 进行总结...")
            
            genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
            client = genai.Client()
            response = client.models.generate_content(
                model=model,
                contents=full_prompt,
                config=genai.types.GenerateContentConfig(
                    temperature=temperature
                )
            )
            
            print("   🎯 Google Gemini 总结完成")
            return response.text.strip()
            
        except Exception as e:
            logger.error(f"Google Gemini API 调用失败: {str(e)}")
            return None


class LocalProvider(AIProviderBase):
    """本地模型 Provider（Ollama 等）
    
    使用本地运行的模型进行 AI 调用，需要配置 LOCAL_API_BASE 环境变量。
    兼容 Ollama、LM Studio 等支持 OpenAI 兼容接口的本地模型服务。
    """
    
    name = "local"
    
    def is_available(self) -> bool:
        api_base = os.getenv("LOCAL_API_BASE")
        return api_base is not None and api_base != ""
    
    def summarize(self, prompt: str, content: str, **kwargs) -> Optional[str]:
        if not self.is_available():
            logger.error("本地模型 API 地址未配置 (LOCAL_API_BASE)")
            return None
        
        try:
            import openai
            
            full_prompt = self._build_full_prompt(prompt, content)
            model = os.getenv("LOCAL_MODEL", "llama3")
            temperature = kwargs.get('temperature', 0.7)
            api_base = os.getenv("LOCAL_API_BASE", "http://localhost:11434/v1")
            
            print(f"   🎯 使用本地模型 ({model}) 进行总结...")
            
            client = openai.OpenAI(
                api_key="ollama",
                base_url=api_base
            )
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": full_prompt}],
                temperature=temperature
            )
            
            print("   🎯 本地模型总结完成")
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            logger.error(f"本地模型调用失败: {str(e)}")
            return None


class MockProvider(AIProviderBase):
    """模拟 Provider（仅用于测试）
    
    不进行真实的 AI 调用，直接返回模拟结果。
    用于开发调试或测试工作流。
    """
    
    name = "mock"
    
    def is_available(self) -> bool:
        return os.getenv("AI_PROVIDER", "") == "mock"
    
    def summarize(self, prompt: str, content: str, **kwargs) -> Optional[str]:
        print("   🎯 [Mock Provider] 模拟 AI 总结...")
        
        mock_result = f"""# 一、文章总结

这是由 Mock Provider 生成的模拟总结结果。

## 1. 概要

本文是对以下内容的结构化总结：
- 内容长度：{len(content)} 字符
- 标签：测试标签

## 2. 核心内容

文章主要讨论了技术相关内容，涵盖了以下要点：
- 第一个要点
- 第二个要点
- 第三个要点

## 3. 总结

本文已通过 Mock Provider 完成模拟总结。
"""
        
        print("   🎯 [Mock Provider] 模拟总结完成")
        return mock_result


class AIProviderManager:
    """AI Provider 管理器
    
    负责管理所有 AI Provider，提供自动检测和切换功能。
    
    外部 Provider（OpenAI/Anthropic/Google/Local）：
    - 需要有显式的 API Key 或 Base URL 配置
    - 自动检测时只检测这些外部 Provider，有配置则自动调用总结
    
    Trae Provider：
    - 使用 Trae SDK 的 llm.create_session() 在同一模型进程内总结
    - 不作为自动检测项，仅当 AI_PROVIDER=trae 显式配置时可用
    - 无外部 Provider 时归入降级流程，由外层对话接手处理
    
    本 SKILL 为通用标准设计，不绑定任何特定平台 SDK。
    """
    
    # 外部 Provider 列表（需要显式配置 API Key）
    EXTERNAL_PROVIDERS = ["openai", "anthropic", "google", "local"]
    
    def __init__(self):
        self._providers = {}
        self._init_providers()
    
    def _init_providers(self):
        """初始化所有 Provider"""
        self._providers = {
            "trae": TraeProvider(),
            "openai": OpenAIProvider(),
            "anthropic": AnthropicProvider(),
            "google": GoogleProvider(),
            "local": LocalProvider(),
            "mock": MockProvider(),
        }
    
    def get_provider(self, provider_name: str = None) -> Optional[AIProviderBase]:
        """获取指定名称的 Provider
        
        Args:
            provider_name: Provider 名称，如果为 None 则自动检测（含全部 Provider）
        
        Returns:
            AIProviderBase: Provider 实例，未找到返回 None
        """
        if provider_name:
            return self._providers.get(provider_name.lower())
        
        configured = os.getenv("AI_PROVIDER", "").lower()
        if configured and configured in self._providers:
            provider = self._providers[configured]
            if provider.is_available():
                return provider
        
        # 自动检测只检测外部 Provider，Trae 需显式指定 AI_PROVIDER=trae
        priority = ["openai", "anthropic", "google", "local"]
        for name in priority:
            provider = self._providers.get(name)
            if provider and provider.is_available():
                print(f"   📋 自动选择 AI Provider: {name}")
                return provider
        
        return None
    
    def get_external_provider(self) -> Optional[AIProviderBase]:
        """获取外部 AI Provider（排除 Trae SDK）
        
        仅检测需要显式 API Key 配置的外部 Provider：
        openai > anthropic > google > local
        Trae Provider 不在此检测范围内，需由外层对话接手处理。
        
        Returns:
            AIProviderBase: 第一个可用的外部 Provider，无则返回 None
        """
        # 优先使用 AI_PROVIDER 环境变量指定的 Provider（如果指定了外部 Provider）
        configured = os.getenv("AI_PROVIDER", "").lower()
        if configured and configured in self.EXTERNAL_PROVIDERS:
            provider = self._providers.get(configured)
            if provider and provider.is_available():
                print(f"   📋 使用配置的外部 AI Provider: {configured}")
                return provider
        
        # 按优先级自动检测外部 Provider
        for name in self.EXTERNAL_PROVIDERS:
            provider = self._providers.get(name)
            if provider and provider.is_available():
                print(f"   📋 自动选择外部 AI Provider: {name}")
                return provider
        
        return None
    
    def has_external_provider(self) -> bool:
        """检查是否有可用的外部 AI Provider"""
        return self.get_external_provider() is not None
    
    def get_available_providers(self) -> list:
        """获取所有可用的 Provider 列表"""
        return [name for name, p in self._providers.items() if p.is_available()]
    
    def get_external_available_providers(self) -> list:
        """获取所有可用的外部 Provider 列表（排除 Trae）"""
        return [name for name in self.EXTERNAL_PROVIDERS if self._providers.get(name) and self._providers[name].is_available()]
    
    def list_all_providers(self) -> dict:
        """列出所有 Provider 及其可用状态"""
        return {
            name: {
                "available": p.is_available(),
                "name": p.name,
                "is_external": name in self.EXTERNAL_PROVIDERS
            }
            for name, p in self._providers.items()
        }


_provider_manager = AIProviderManager()


def get_ai_provider(provider_name: str = None) -> Optional[AIProviderBase]:
    """获取 AI Provider 的快捷函数
    
    Args:
        provider_name: Provider 名称，为空则自动检测（含全部 Provider）
    
    Returns:
        AIProviderBase: Provider 实例
    """
    return _provider_manager.get_provider(provider_name)


def get_external_ai_provider() -> Optional[AIProviderBase]:
    """获取外部 AI Provider（排除 Trae SDK）
    
    仅检测需要显式 API Key 配置的外部 Provider：
    openai > anthropic > google > local
    若无配置则返回 None，触发降级流程让外层对话接手。
    
    Returns:
        AIProviderBase: 第一个可用的外部 Provider，无则返回 None
    """
    return _provider_manager.get_external_provider()


def has_external_provider() -> bool:
    """检查是否有可用的外部 AI Provider"""
    return _provider_manager.has_external_provider()


def list_available_providers() -> list:
    """获取所有可用的 Provider 列表"""
    return _provider_manager.get_available_providers()


def list_external_providers() -> list:
    """获取所有可用的外部 Provider 列表（排除 Trae）"""
    return _provider_manager.get_external_available_providers()


def call_ai_summarize(prompt: str, content: str, provider_name: str = None, **kwargs) -> Optional[str]:
    """调用 AI 进行总结的快捷函数
    
    Args:
        prompt: 提示词模板
        content: 需要总结的内容
        provider_name: 指定 Provider 名称，为空则自动选择
        **kwargs: 其他参数（如 temperature）
    
    Returns:
        str: 总结结果，失败返回 None
    """
    provider = get_ai_provider(provider_name)
    if provider is None:
        logger.error("没有可用的 AI Provider")
        return None
    
    return provider.summarize(prompt, content, **kwargs)


def call_external_ai_summarize(prompt: str, content: str, **kwargs) -> Optional[str]:
    """调用外部 AI Provider 进行总结（排除 Trae SDK）
    
    仅检测外部 Provider（OpenAI/Anthropic/Google/Local），
    若均不可用则返回 None，触发 skill_main 中的降级流程。
    
    Args:
        prompt: 提示词模板
        content: 需要总结的内容
        **kwargs: 其他参数（如 temperature）
    
    Returns:
        str: 总结结果，无可用外部 Provider 返回 None
    """
    provider = get_external_ai_provider()
    if provider is None:
        logger.info("没有可用的外部 AI Provider，将触发降级流程")
        return None
    
    return provider.summarize(prompt, content, **kwargs)
