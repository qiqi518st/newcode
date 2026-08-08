"""自定义异常类型"""


class MewCodeError(Exception):
    """MewCode 所有异常的基类"""
    pass


class ConfigError(MewCodeError):
    """配置相关的错误：文件不存在、字段缺失、格式错误、protocol 非法等"""
    pass


class ProviderError(MewCodeError):
    """Provider 调用相关的错误：API 错误、网络错误等"""
    pass


class ToolError(MewCodeError):
    """工具执行失败的基类"""
    pass


class CommandNotAllowedError(ToolError):
    """命令不在白名单"""
    pass


class PathTraversalError(ToolError):
    """路径越界，超出项目范围"""
    pass


class ToolTimeoutError(ToolError):
    """工具执行超时"""
    pass