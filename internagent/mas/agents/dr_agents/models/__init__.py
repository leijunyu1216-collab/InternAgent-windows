import os

from .base_model import BaseModel
from .deepseek import DeepSeekModel
from .openai_model import OpenAIModel
from .vllm_qwen import QwenModel
from .intern_s1 import InternS1Model
from .qwen_model import QwenAPIModel
from .gemini_model import GeminiModel

def get_model(model_name: str, **kwargs) -> BaseModel:
    """
    获取模型实例
    
    Args:
        model_name: 模型名称
        **kwargs: 模型初始化参数
        
    Returns:
        模型实例
    """
    if "deepseek" in model_name:
        return DeepSeekModel(model_name, **kwargs)
    elif model_name.startswith("gpt") or model_name.startswith("o"):
        return OpenAIModel(model_name, **kwargs)
    elif model_name.startswith("Qwen/") or model_name.startswith("qwen"):
        return QwenAPIModel(model_name, **kwargs)
    elif model_name.startswith("Qwen") or model_name.startswith("vllm"):
        return QwenModel(model_name, **kwargs)
    elif model_name.startswith("intern"):
        return InternS1Model(model_name, **kwargs)
    elif model_name.startswith("gemini"):
        return GeminiModel(model_name, **kwargs)
    elif os.getenv("OPENAI_API_BASE_URL"):
        # Local patch: route any OpenAI-compatible vendor (Kimi, GLM, ...)
        # through the generic client instead of failing on an unknown prefix.
        return OpenAIModel(model_name, **kwargs)
    else:
        raise ValueError(f"Unsupported model: {model_name}")


__all__ = ['BaseModel', 'DeepSeekModel', 'OpenAIModel', 'get_model', 'QwenModel', 'InternS1Model', 'GeminiModel'] 