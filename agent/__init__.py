# XXXX Customer Service Agent - 基于 Claude Agent SDK
from .agent import CustomerServiceAgent, CustomerServiceAgentSync, quick_chat, MemoryMode
from .hooks import MemoryManager

__version__ = "0.1.0"
__all__ = [
    "CustomerServiceAgent",           # 异步版本
    "CustomerServiceAgentSync",       # 同步包装版本
    "quick_chat",              # 便捷函数
    "MemoryManager",   # 内存管理器
    "MemoryMode",              # 记忆模式类型
]
