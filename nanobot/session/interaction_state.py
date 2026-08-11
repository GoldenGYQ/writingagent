"""
Durable human-in-the-loop interaction state for one chat session.

这个模块管理单个聊天会话中的人机交互状态，支持：
- 交互请求的创建、存储和查询
- 用户响应的验证和处理
- 与工作计划的联动更新
- WebSocket状态推送的数据准备
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, MutableMapping, cast

from nanobot.session.working_plan import WORKING_PLAN_KEY, parse_working_plan

# ============================================================================
# 常量定义
# ============================================================================

# 在metadata字典中存储交互状态使用的键名
INTERACTION_STATE_KEY = "interaction_request"


# ============================================================================
# 数据获取与解析函数
# ============================================================================

def interaction_raw(metadata: Mapping[str, Any] | None) -> Any:
    """
    从metadata中原始获取交互状态数据，不做任何验证。

    这个函数是最底层的读取操作，直接返回存储的原始值。

    Args:
        metadata: 会话的元数据字典，可能为None

    Returns:
        存储在INTERACTION_STATE_KEY键下的原始数据，如果metadata为None或键不存在则返回None
    """
    return metadata.get(INTERACTION_STATE_KEY) if metadata else None


def parse_interaction(value: Any) -> dict[str, Any] | None:
    """
    安全地将任意值解析为字典类型的交互数据。

    这个函数只做类型检查，不验证数据内容的有效性。

    Args:
        value: 从metadata中获取的原始数据

    Returns:
        如果value是字典则返回它本身（类型安全），否则返回None
    """
    return cast(dict[str, Any], value) if isinstance(value, dict) else None


def pending_interaction(metadata: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """
    获取当前待处理的交互请求（状态为'pending'）。

    这是最常用的查询函数，用于检查是否有等待用户响应的交互。

    Args:
        metadata: 会话的元数据字典

    Returns:
        如果存在状态为'pending'的交互请求则返回该字典，否则返回None
    """
    # 1. 获取原始数据
    raw_data = interaction_raw(metadata)

    # 2. 解析为字典
    request = parse_interaction(raw_data)

    # 3. 检查是否存在且状态为'pending'
    if not request or request.get("status") != "pending":
        return None

    return request


def waiting_for_user(metadata: Mapping[str, Any] | None) -> bool:
    """
    判断当前会话是否正在等待用户输入。

    这是一个便捷的布尔检查函数，用于快速判断会话状态。

    Args:
        metadata: 会话的元数据字典

    Returns:
        如果有待处理的交互请求则返回True，否则返回False
    """
    return pending_interaction(metadata) is not None


def interaction_ws_blob(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """
    生成用于WebSocket推送的交互状态数据。

    这个函数为前端准备标准化的交互状态数据，确保前端始终接收到一致格式的数据。

    Args:
        metadata: 会话的元数据字典

    Returns:
        包含交互状态的数据字典：
        - 如果有待处理请求：返回请求的副本
        - 如果没有：返回 {"pending": False}
    """
    request = pending_interaction(metadata)

    # 如果有待处理请求，返回副本以避免外部修改影响内部状态
    if request:
        return {key: value for key, value in request.items() if not str(key).startswith("_")}

    # 没有待处理请求时返回标准格式
    return {"pending": False}


# ============================================================================
# 核心功能：解析交互请求
# ============================================================================

def resolve_interaction(
    metadata: MutableMapping[str, Any],
    *,
    interaction_id: str,
    action: str,
    values: Mapping[str, Any],
) -> dict[str, Any]:
    """
    解析待处理的交互请求，处理用户提交的响应。

    这是模块的核心函数，负责：
    1. 验证交互请求的存在性和有效性
    2. 验证用户选择的操作是否合法
    3. 验证用户提交的字段值和类型
    4. 更新交互状态
    5. 联动更新工作计划状态

    Args:
        metadata: 会话的可变元数据字典（会被修改）
        interaction_id: 交互请求的ID，用于防止处理过期的请求
        action: 用户选择的操作ID
        values: 用户提交的字段值字典，键为字段ID，值为用户输入

    Returns:
        解析后的交互状态字典

    Raises:
        ValueError: 在以下情况抛出：
            - 没有待处理的交互请求
            - 交互ID不匹配（请求已过期）
            - 操作ID不存在
            - 字段验证失败（未知字段、缺失必填字段、类型错误、选项值不合法）

    Example:
        >>> metadata = {"interaction_request": {...}}  # 包含待处理请求
        >>> resolved = resolve_interaction(
        ...     metadata,
        ...     interaction_id="req_123",
        ...     action="confirm",
        ...     values={"name": "Alice", "age": 30}
        ... )
    """

    # ========================================================================
    # 第一步：验证交互请求的存在性和有效性
    # ========================================================================

    # 获取待处理的交互请求
    request = pending_interaction(metadata)
    if not request:
        # 没有正在等待的交互请求
        raise ValueError("no interaction is waiting for user input")

    # 验证交互ID是否匹配（防止处理过期的请求）
    # 使用str()确保类型安全，空字符串作为默认值
    if str(request.get("id") or "") != interaction_id:
        raise ValueError("interaction request is stale")

    # ========================================================================
    # 第二步：验证用户选择的操作
    # ========================================================================

    # 从请求中获取可用的操作列表
    actions = request.get("actions")

    # 构建操作ID到操作定义的映射字典
    # 只处理包含'id'字段的字典项，确保数据完整性
    action_items: dict[str, dict[str, Any]] = {}
    if isinstance(actions, list):
        for raw_action in cast(list[Any], actions):
            if not isinstance(raw_action, dict):
                continue
            item = cast(dict[str, Any], raw_action)
            if item.get("id"):
                action_items[str(item["id"])] = item

    # 查找用户选择的操作
    selected_action = action_items.get(action)
    if selected_action is None:
        # 用户选择了不存在的操作
        raise ValueError("unknown interaction action")

    # ========================================================================
    # 第三步：验证用户提交的字段值
    # ========================================================================

    # 从请求中获取字段定义列表
    fields = request.get("fields")

    # 只在字段定义存在且为列表时进行验证
    if isinstance(fields, list):
        # 构建字段ID到字段定义的映射
        typed_fields = cast(list[Any], fields)
        field_items: dict[str, dict[str, Any]] = {}
        for raw_field in typed_fields:
            if not isinstance(raw_field, dict):
                continue
            field_item = cast(dict[str, Any], raw_field)
            if field_item.get("id"):
                field_items[str(field_item["id"])] = field_item

        # 3.1 检查是否有未定义的字段
        # 用户提交的字段必须在字段定义中存在
        unknown_fields = set(values) - set(field_items)
        if unknown_fields:
            # 只报告第一个未知字段，减少错误信息的复杂度
            raise ValueError(f"unknown interaction field: {sorted(unknown_fields)[0]}")

        # 3.2 逐个验证每个字段
        for raw in typed_fields:
            # 跳过非字典类型的字段定义（防御性编程）
            if not isinstance(raw, dict):
                continue

            field = cast(dict[str, Any], raw)
            field_id = str(field.get("id") or "")
            value = values.get(field_id)

            # 3.2.1 验证必填字段
            # 只有字段标记为required且操作样式为primary时才强制必填
            requires_value = bool(field.get("required")) and selected_action.get("style") == "primary"
            if requires_value:
                # 检查值是否为空（None、空字符串、空列表、False都被视为空）
                is_empty = (
                    value is None or
                    value == "" or
                    value == [] or
                    value is False
                )
                if is_empty:
                    raise ValueError(f"missing required field: {field_id}")

            # 如果用户没有提交该字段的值，跳过后续的类型验证
            if field_id not in values:
                continue

            # 3.2.2 验证字段类型
            field_type = str(field.get("type") or "text")

            # 文本类型字段：必须是字符串
            if field_type in {"text", "textarea"}:
                if not isinstance(value, str):
                    raise ValueError(f"invalid value for field: {field_id}")

            # 确认类型字段：必须是布尔值
            if field_type == "confirm":
                if not isinstance(value, bool):
                    raise ValueError(f"invalid value for field: {field_id}")

            # 3.2.3 验证选项字段（select、radio、checkbox）
            # 提取允许的选项值集合
            options = field.get("options")
            allowed_values: set[str] = set()
            if isinstance(options, list):
                for raw_option in cast(list[Any], options):
                    if not isinstance(raw_option, dict):
                        continue
                    option = cast(dict[str, Any], raw_option)
                    if option.get("value") is not None:
                        allowed_values.add(str(option["value"]))

            # 单选/下拉选择：值必须是字符串且在允许值集合中
            if field_type in {"select", "radio"}:
                if not isinstance(value, str) or value not in allowed_values:
                    raise ValueError(f"invalid value for field: {field_id}")

            # 复选框：值必须是字符串列表且每个值都在允许值集合中
            if field_type == "checkbox":
                selected_values = cast(list[Any], value) if isinstance(value, list) else None
                if (
                    selected_values is None
                    or any(
                        not isinstance(item, str) or item not in allowed_values
                        for item in selected_values
                    )
                ):
                    raise ValueError(f"invalid value for field: {field_id}")

    # ========================================================================
    # 第四步：生成时间戳并更新交互状态
    # ========================================================================

    # 获取当前时间的ISO格式字符串（带时区信息）
    now = datetime.now().astimezone().isoformat()

    # 构建解析后的状态对象
    resolved = {
        **request,  # 保留原请求的所有字段
        "pending": False,  # 标记为已处理
        "status": "resolved" if action != "cancel" else "cancelled",  # 根据操作设置状态
        "resolved_at": now,  # 记录解析时间
        "response": {
            "action": action,  # 用户选择的操作
            "values": dict(values),  # 用户提交的值（转换为普通字典）
        },
    }

    # 更新metadata中的交互状态
    metadata[INTERACTION_STATE_KEY] = resolved

    # ========================================================================
    # 第五步：联动更新工作计划状态
    # ========================================================================

    # 获取并解析工作计划
    plan = parse_working_plan(metadata.get(WORKING_PLAN_KEY))

    # 如果工作计划存在且状态为"waiting_for_user"（等待用户输入）
    if plan and plan.get("status") == "waiting_for_user":
        # 创建工作计划的更新版本
        updated_plan = dict(plan)

        # 根据用户操作更新计划状态
        # - 如果用户取消了操作：计划状态变为"cancelled"
        # - 如果用户确认了操作：计划状态变为"active"（继续执行）
        updated_plan["status"] = "cancelled" if action == "cancel" else "active"

        # 增加版本号（用于追踪变更历史）
        updated_plan["version"] = int(plan.get("version", 1)) + 1

        # 更新时间戳
        updated_plan["updated_at"] = now

        # 更新metadata中的工作计划
        metadata[WORKING_PLAN_KEY] = updated_plan

    # 返回解析后的交互状态
    return resolved
