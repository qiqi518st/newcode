"""自定义异常类型"""


class NewCodeError(Exception):
    """NewCode 所有异常的基类"""


class ConfigError(NewCodeError):
    """配置相关的错误：文件不存在、字段缺失、格式错误、protocol 非法等"""


class ProviderError(NewCodeError):
    """Provider 调用相关的错误：API 错误、网络错误等"""


class ToolError(NewCodeError):
    """工具执行失败的基类"""


class CommandNotAllowedError(ToolError):
    """命令不在白名单"""


class PathTraversalError(ToolError):
    """路径越界，超出项目范围"""


class ToolTimeoutError(ToolError):
    """工具执行超时"""
