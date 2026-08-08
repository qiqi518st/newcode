"""pytest 配置：anyio 自动处理 async 测试"""

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"
