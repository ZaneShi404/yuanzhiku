"""确定性派生身份（加固计划 Task 7）。

自动串联的后继作业、自动写入的表示、下载创建的来源/版本使用固定命名
空间的 UUIDv5：相同输入永远得到相同 32 位小写十六进制 ID。仓储层以
insert-or-return 语义消费这些 ID，业务写入后、作业终态前发生故障时，
重试重放写入不再产生重复行；手工再次触发的作业仍使用随机新 ID。
"""

from __future__ import annotations

import uuid

# 固定命名空间（uuid5 of "yuanzhiku:identity:v1" under NAMESPACE_URL），
# 变更即全局换身份，绝不改动。
_IDENTITY_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "yuanzhiku:identity:v1")


def derived_identifier(namespace: str, *parts: str) -> str:
    """返回固定命名空间 UUIDv5 的 32 位小写十六进制。"""
    if not namespace or not parts or not all(parts):
        raise ValueError("派生身份需要命名空间与非空组成")
    return uuid.uuid5(_IDENTITY_NAMESPACE, ":".join((namespace, *parts))).hex
