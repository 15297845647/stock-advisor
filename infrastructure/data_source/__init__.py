"""数据源模块 — 多源降级链

对外暴露：
    DataSource            — 抽象基类
    DataSourceManager     — 降级管理器（业务层直接使用）
    build_data_manager    — 全局单例工厂
    get_data_manager      — 获取全局单例
"""

from infrastructure.data_source.base import DataSource
from infrastructure.data_source.manager import DataSourceManager
from infrastructure.data_source.factory import (
    build_data_manager, get_data_manager, reload_data_manager,
)

__all__ = [
    "DataSource",
    "DataSourceManager",
    "build_data_manager",
    "get_data_manager",
    "reload_data_manager",
]
