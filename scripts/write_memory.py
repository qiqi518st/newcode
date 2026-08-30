import sys
from pathlib import Path

sys.path.insert(0, r"D:\code\newcode")

from mewcode.memory.models import MemoryOperation
from mewcode.memory.store import MemoryStore

store = MemoryStore(Path.home() / ".mewcode" / "memory")
op = MemoryOperation(
    action="create",
    level="user",
    type="user_preference",
    title="Python 初学者",
    slug="python_beginner",
    content="用户是 Python 新手，正在学习 Python。讲解代码时请使用简单直白的语言，避免使用未解释的专业术语。",
)
store.apply(op, source_session="manual")
print("index:", store.index_path)
print(store.load_index())
