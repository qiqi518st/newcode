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