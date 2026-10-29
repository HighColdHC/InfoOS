"""Tasks module: spec §1 任务模型。

公开 API:
  - TaskType / TaskStatus: 任务类型与状态枚举
  - Task / DependencySpec: 任务与依赖 dataclass
  - TaskRepository: 任务队列 CRUD
"""
from backend.application.tasks.types import Task, TaskStatus, TaskType, DependencySpec
from backend.application.tasks.repository import TaskRepository

__all__ = ["Task", "TaskType", "TaskStatus", "TaskRepository", "DependencySpec"]
