"""面板数据源公共异常"""


class ProfileError(Exception):
    """面板数据获取/解析失败（数据源不可用、UID 未打开展示柜等），消息为中文提示"""

    def __init__(self, message: str, source: str = ""):
        super().__init__(message)
        # 数据源标识：enka / mihomo
        self.source = source
