"""Failures module: spec §10 失败矩阵的代码承载。

公开 API:
  - FetchFailureReason: durable failure reason enum
  - classify_failure: 异常 → 原因
  - get_retry_policy: 原因 → 重试策略
  - get_notification: 原因 → 用户通知
  - FetchContext, ClassificationError: 上下文与异常

新增模块时必须：
  1. 在对应文件中实现
  2. 在本 __init__.py 中 re-export
  3. 加测试
"""
from backend.domain.failures.classifier import (
    ClassificationError,
    FetchContext,
    classify_failure,
)
from backend.domain.failures.notifications import (
    NOTIFICATION_COPY,
    Notification,
    NotificationLevel,
    get_notification,
)
from backend.domain.failures.retry_policy import (
    RETRY_POLICIES,
    RetryPolicy,
    RetryStrategy,
    get_retry_policy,
)
from backend.domain.failures.taxonomy import FetchFailureReason
from backend.domain.failures.contention import (
    OpenCLISiteLeaseTimeout,
    ResourceContentionError,
    ResourceLeaseLost,
)
from backend.domain.failures.facts import FailureFact, RecoveryDecision

__all__ = [
    "FetchFailureReason",
    "FetchContext",
    "ClassificationError",
    "classify_failure",
    "RetryStrategy",
    "RetryPolicy",
    "RETRY_POLICIES",
    "get_retry_policy",
    "NotificationLevel",
    "Notification",
    "NOTIFICATION_COPY",
    "get_notification",
    "ResourceContentionError",
    "ResourceLeaseLost",
    "OpenCLISiteLeaseTimeout",
    "FailureFact",
    "RecoveryDecision",
]
