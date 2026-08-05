"""
Session metadata helpers for durable, goal-independent working plans.

这个模块提供会话元数据中工作计划（Working Plan）的辅助函数。
工作计划是持久化的、与目标无关的执行计划，用于跟踪长期任务的执行状态。

主要功能：
- 工作计划的存储和查询
- 工作计划状态的判断
- 生成工作计划的可读摘要
- 为WebSocket推送准备工作计划数据
"""

from __future__ import annotations

from typing import Any, Mapping, cast

# ============================================================================
# 常量定义
# ============================================================================

# 在metadata字典中存储工作计划使用的键名
WORKING_PLAN_KEY = "working_plan"

# 在生成工作计划摘要时，最多显示的执行步骤数量
# 限制数量可以避免输出过长，保持可读性
_MAX_RUNTIME_STEPS = 30


# ============================================================================
# 数据获取与解析函数
# ============================================================================

def parse_working_plan(value: Any) -> dict[str, Any] | None:
    """
    安全地将任意值解析为字典类型的工作计划数据。
    
    这是一个基础的类型安全转换函数，只做类型检查，不验证数据内容。
    
    Args:
        value: 从metadata中获取的原始数据（可能是任何类型）
        
    Returns:
        如果value是字典则返回它本身（类型安全），否则返回None
        
    Example:
        >>> plan = parse_working_plan({"id": "plan_123", "status": "active"})
        >>> print(plan["status"])
        active
    """
    return cast(dict[str, Any], value) if isinstance(value, dict) else None


def working_plan_raw(metadata: Mapping[str, Any] | None) -> Any:
    """
    从metadata中原始获取工作计划数据，不做任何验证。
    
    这是最底层的读取操作，直接返回存储的原始值。
    
    Args:
        metadata: 会话的元数据字典，可能为None
        
    Returns:
        存储在WORKING_PLAN_KEY键下的原始数据，如果metadata为None或键不存在则返回None
    """
    return metadata.get(WORKING_PLAN_KEY) if metadata else None


# ============================================================================
# 状态查询函数
# ============================================================================

def active_working_plan(metadata: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """
    获取当前活跃的工作计划。
    
    工作计划必须在以下状态之一才被认为是活跃的：
    - draft: 草稿状态，正在准备中
    - active: 正在执行中
    - waiting_for_user: 等待用户输入（需要人机交互）
    
    注意：cancelled（已取消）和 completed（已完成）状态不被认为是活跃的。
    
    Args:
        metadata: 会话的元数据字典
        
    Returns:
        如果存在活跃的工作计划则返回该字典，否则返回None
        
    Example:
        >>> metadata = {"working_plan": {"id": "plan_123", "status": "active"}}
        >>> plan = active_working_plan(metadata)
        >>> if plan:
        ...     print(f"Plan {plan['id']} is active")
    """
    # 1. 获取原始数据
    raw_data = working_plan_raw(metadata)
    
    # 2. 解析为字典
    plan = parse_working_plan(raw_data)
    
    # 3. 检查状态是否为活跃状态
    # 只有 draft、active、waiting_for_user 这三种状态被视为活跃
    if not plan or plan.get("status") not in {"draft", "active", "waiting_for_user"}:
        return None
    
    return plan


# ============================================================================
# 摘要生成函数
# ============================================================================

def working_plan_runtime_lines(metadata: Mapping[str, Any] | None) -> list[str]:
    """
    生成工作计划运行时状态的文本摘要。
    
    这个函数主要用于：
    - 日志输出，方便调试和监控
    - 向用户展示当前计划的执行进度
    - 生成可读性的状态报告
    
    摘要内容包括：
    - 计划的基本信息（ID、版本、类型、状态、标题、目标）
    - 执行步骤列表（限制显示数量以避免过长）
    
    Args:
        metadata: 会话的元数据字典
        
    Returns:
        字符串列表，每行是一个摘要条目。如果没有活跃计划，返回空列表。
        
    Example:
        >>> metadata = {"working_plan": {
        ...     "id": "plan_123",
        ...     "version": 3,
        ...     "kind": "research",
        ...     "status": "active",
        ...     "title": "Research Project",
        ...     "objective": "Find best practices",
        ...     "steps": [
        ...         {"id": "step_1", "status": "completed", "title": "Literature review"},
        ...         {"id": "step_2", "status": "in_progress", "title": "Data collection"}
        ...     ]
        ... }}
        >>> lines = working_plan_runtime_lines(metadata)
        >>> for line in lines:
        ...     print(line)
        Working plan (durable runtime state):
        Plan ID: plan_123
        Version: 3
        Kind: research
        Status: active
        Title: Research Project
        Objective: Find best practices
        Steps:
        - [completed] step_1: Literature review
        - [in_progress] step_2: Data collection
    """
    # 1. 获取活跃的工作计划
    plan = active_working_plan(metadata)
    
    # 如果没有活跃计划，返回空列表
    if not plan:
        return []
    
    # 2. 构建计划的基本信息部分
    lines = [
        "Working plan (durable runtime state):",  # 标题行
        f"Plan ID: {plan.get('id', '')}",         # 计划唯一标识
        f"Version: {plan.get('version', 1)}",     # 版本号（每次更新递增）
        f"Kind: {plan.get('kind', 'general')}",   # 计划类型（如：research, coding, analysis等）
        f"Status: {plan.get('status', 'active')}", # 当前状态
        f"Title: {plan.get('title', '')}",        # 计划标题
        f"Objective: {plan.get('objective', '')}", # 计划目标描述
        "Steps:",                                  # 步骤列表标题
    ]
    
    # 3. 获取执行步骤列表
    steps = plan.get("steps")
    
    # 4. 检查步骤列表是否有效且非空
    if not isinstance(steps, list) or not steps:
        # 没有步骤记录时显示提示信息
        lines.append("- (no steps recorded)")
        return lines
    
    # 5. 遍历步骤（只显示前_MAX_RUNTIME_STEPS个，防止输出过长）
    for raw in steps[:_MAX_RUNTIME_STEPS]:
        # 跳过非字典类型的步骤（防御性编程）
        if not isinstance(raw, dict):
            continue
        
        # 类型安全转换
        step = cast(dict[str, Any], raw)
        
        # 格式化每个步骤：
        # - [状态] ID: 标题
        # 例如：- [completed] step_1: Literature review
        lines.append(
            f"- [{step.get('status', 'pending')}] {step.get('id', '')}: {step.get('title', '')}"
        )
    
    # 返回完整的摘要行列表
    return lines


# ============================================================================
# WebSocket数据准备函数
# ============================================================================

def working_plan_ws_blob(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """
    生成用于WebSocket推送的工作计划数据。
    
    这个函数为前端准备标准格式的工作计划数据，确保：
    - 数据格式一致，方便前端解析
    - 如果有计划，返回计划数据的副本（防止外部修改）
    - 如果没有计划，返回标准格式的"无计划"状态
    
    Args:
        metadata: 会话的元数据字典
        
    Returns:
        工作计划数据字典：
        - 如果有计划：返回计划的完整副本
        - 如果没有：返回 {"active": False}
        
    Example:
        >>> metadata = {"working_plan": {"id": "plan_123", "status": "active", "steps": []}}
        >>> blob = working_plan_ws_blob(metadata)
        >>> print(blob["id"])
        plan_123
        
        >>> metadata = {}
        >>> blob = working_plan_ws_blob(metadata)
        >>> print(blob)
        {'active': False}
    """
    # 1. 获取并解析工作计划
    raw_data = working_plan_raw(metadata)
    plan = parse_working_plan(raw_data)
    
    # 2. 如果有计划，返回副本（避免外部代码修改内部状态）
    if plan:
        return dict(plan)
    
    # 3. 没有计划时返回标准格式
    return {"active": False}