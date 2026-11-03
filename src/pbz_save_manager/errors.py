"""管理器对外抛出的可展示错误。"""


class SaveToolError(Exception):
    """界面可以直接显示给用户的错误。"""


class FormatError(SaveToolError):
    """存档容器无法按已知格式读取。"""


class EditError(SaveToolError):
    """修改被拒绝，原文件没有被写入。"""


class GuardError(SaveToolError):
    """为避免和游戏同时写档而停止。"""
