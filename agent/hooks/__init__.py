"""XXXX Hooks - Memory integration hooks for Claude Agent SDK"""

from .memory_manager import MemoryManager
from .memory_hooks import (
    user_prompt_submit_hook,
    stop_hook,
    set_memory_manager,
    get_memory_manager,
)

__all__ = [
    "MemoryManager",
    "user_prompt_submit_hook",
    "stop_hook",
    "set_memory_manager",
    "get_memory_manager",
]
