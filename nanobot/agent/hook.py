"""Shared lifecycle hook primitives for agent runs."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.providers.base import LLMResponse, ToolCallRequest


@dataclass(slots=True)
class AgentHookContext:
    """Mutable per-iteration state exposed to runner hooks."""

    iteration: int
    messages: list[dict[str, Any]]
    response: LLMResponse | None = None
    usage: dict[str, int] = field(default_factory=dict)
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    tool_results: list[Any] = field(default_factory=list)
    tool_events: list[dict[str, str]] = field(default_factory=list)
    streamed_content: bool = False
    streamed_reasoning: bool = False
    stream_continues_current_message: bool = False
    final_content: str | None = None
    stop_reason: str | None = None
    error: str | None = None
    session_key: str | None = None


@dataclass(slots=True)
class AgentRunHookContext:
    """Run-level state snapshot exposed to runner hooks."""

    messages: list[dict[str, Any]]
    final_content: str | None = None
    tools_used: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    stop_reason: str | None = None
    error: str | None = None
    tool_events: list[dict[str, str]] = field(default_factory=list)
    had_injections: bool = False
    exception: BaseException | None = None


@dataclass(slots=True)
class AgentTurnHookContext:
    """Turn-local inputs available when constructing per-turn hooks."""

    on_progress: Callable[..., Awaitable[None]] | None = None
    workspace: Path | None = None
    channel: str = "cli"
    chat_id: str = "direct"
    message_id: str | None = None
    session_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    ephemeral: bool = False
    attributes: dict[str, Any] = field(default_factory=dict)


class AgentHook:
    """
    Agent钩子基类，提供生命周期扩展点。
    
    这个类定义了Agent执行过程中的所有钩子方法，允许外部代码在关键节点介入
    Agent的执行流程。所有方法都有默认实现（空操作或简单返回），子类可以
    选择性重写需要的方法。
    
    设计理念：
    1. 最小化侵入：默认实现为空，不影响正常执行
    2. 生命周期完整：覆盖从开始到结束的所有关键节点
    3. 流式支持：支持流式输出的实时处理
    4. 工具调用监控：提供工具执行全过程的钩子
    
    Attributes:
        _reraise: 是否在钩子异常时重新抛出（默认False，即吞掉异常）
    """

    def __init__(self, reraise: bool = False) -> None:
        """
        初始化钩子实例。
        
        Args:
            reraise: 控制钩子异常处理策略
                - True: 钩子抛出异常时，中断Agent执行并重新抛出
                - False: 钩子抛出异常时，记录日志但继续执行（默认）
        """
        self._reraise = reraise

    # ========================================================================
    # 基础配置方法
    # ========================================================================

    def wants_streaming(self) -> bool:
        """
        声明钩子是否需要流式输出支持。
        
        如果返回True，Agent将在生成过程中调用 on_stream() 方法，
        实现实时输出。如果返回False，Agent将批量生成结果后一次性输出。
        
        Returns:
            bool: 是否需要流式支持，默认False
        """
        return False

    # ========================================================================
    # 主要生命周期钩子
    # ========================================================================

    async def before_run(self, context: AgentRunHookContext) -> None:
        """
        在Agent核心执行前调用。
        
        可用于：
        - 修改或过滤初始消息
        - 设置运行时变量
        - 权限验证
        - 记录开始日志
        - 初始化资源
        
        Args:
            context: 运行上下文，包含初始消息列表
        """
        pass

    async def after_run(self, context: AgentRunHookContext) -> None:
        """
        在Agent核心执行完成后调用（成功情况下）。
        
        可用于：
        - 处理或转换运行结果
        - 记录性能指标
        - 触发后续工作流
        - 格式化最终输出
        
        Args:
            context: 运行上下文，包含完整的执行结果
        """
        pass

    async def on_error(self, context: AgentRunHookContext) -> None:
        """
        在Agent执行出错时调用。
        
        可用于：
        - 记录详细错误日志
        - 发送告警通知
        - 进行错误恢复尝试
        - 清理资源
        
        Args:
            context: 运行上下文，包含错误信息
        """
        pass

    async def on_finally(self, context: AgentRunHookContext) -> None:
        """
        在Agent执行结束时调用（无论成功还是失败）。
        
        这是清理资源的最后机会，保证在finally块中执行。
        
        可用于：
        - 释放文件句柄、数据库连接
        - 删除临时文件
        - 记录审计日志
        - 发送完成通知
        
        Args:
            context: 运行上下文，包含最终状态
        """
        pass

    # ========================================================================
    # 迭代级别钩子（每次LLM调用）
    # ========================================================================

    async def before_iteration(self, context: AgentHookContext) -> None:
        """
        在每次迭代（LLM调用）前调用。
        
        多轮对话中，每次调用LLM前都会触发此钩子。
        
        可用于：
        - 动态注入系统提示
        - 调整温度等参数
        - 记录迭代开始
        
        Args:
            context: 钩子上下文，包含当前消息状态
        """
        pass

    async def after_iteration(self, context: AgentHookContext) -> None:
        """
        在每次迭代（LLM调用）后调用。
        
        可用于：
        - 处理每次迭代的结果
        - 检查是否应该继续
        - 记录迭代完成
        
        Args:
            context: 钩子上下文，包含当前消息状态
        """
        pass

    # ========================================================================
    # 流式输出钩子
    # ========================================================================

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        """
        在流式输出时，收到每个增量文本块时调用。
        
        需要先通过 wants_streaming() 声明启用流式支持。
        
        可用于：
        - 实时转发到WebSocket
        - 实时累积并分析
        - 实时展示给用户
        
        Args:
            context: 钩子上下文
            delta: 本次增量的文本内容
        """
        pass

    async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
        """
        在流式输出结束时调用。
        
        Args:
            context: 钩子上下文
            resuming: 是否是因为恢复而结束
                - True: 流被暂停后恢复，继续输出
                - False: 流正常结束
        """
        pass

    # ========================================================================
    # 推理内容钩子（用于思维链等）
    # ========================================================================

    async def emit_reasoning(self, reasoning_content: str | None) -> None:
        """
        发送推理内容（如思维链）。
        
        可用于：
        - 显示模型的思考过程
        - 收集推理数据
        - 调试分析
        
        Args:
            reasoning_content: 推理内容文本，None表示清空
        """
        pass

    async def emit_reasoning_end(self) -> None:
        """
        标记推理流结束。
        
        用于缓冲推理内容的钩子（如为了UI更新），在此处刷新和冻结。
        一次性钩子可以忽略此方法。
        """
        pass

    # ========================================================================
    # 工具调用相关钩子
    # ========================================================================

    async def on_provider_tool_event(
        self,
        context: AgentHookContext,
        event: dict[str, Any],
    ) -> None:
        """
        观察Provider托管的工具生命周期事件。
        
        用于监控外部工具的执行状态，如：
        - 工具开始执行
        - 工具执行进度
        - 工具执行完成
        
        Args:
            context: 钩子上下文
            event: 工具事件数据，包含事件类型和详细信息
        """
        pass

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        """
        在批量执行工具前调用。
        
        可用于：
        - 预处理工具调用列表
        - 检查工具权限
        - 记录开始执行
        
        Args:
            context: 钩子上下文，包含待执行的工具调用
        """
        pass

    async def before_execute_tool(
        self,
        context: AgentHookContext,
        tool_call: ToolCallRequest,
        tool: Any,
        params: Any,
    ) -> None:
        """
        在单个工具执行前调用。
        
        可用于：
        - 验证工具调用参数
        - 修改工具参数
        - 记录工具执行开始
        
        Args:
            context: 钩子上下文
            tool_call: 工具调用请求对象
            tool: 工具实例
            params: 工具参数
        """
        pass

    async def after_execute_tool(
        self,
        context: AgentHookContext,
        tool_call: ToolCallRequest,
        tool: Any,
        params: Any,
        result: Any,
    ) -> None:
        """
        在单个工具执行完成后调用（成功情况下）。
        
        可用于：
        - 处理或转换工具结果
        - 验证工具输出
        - 记录工具执行指标
        
        Args:
            context: 钩子上下文
            tool_call: 工具调用请求对象
            tool: 工具实例
            params: 工具参数
            result: 工具执行结果
        """
        pass

    async def on_execute_tool_error(
        self,
        context: AgentHookContext,
        tool_call: ToolCallRequest,
        tool: Any,
        params: Any,
        error: Any,
    ) -> None:
        """
        在单个工具执行出错时调用。
        
        可用于：
        - 记录工具错误日志
        - 尝试降级或重试
        - 向用户反馈错误信息
        
        Args:
            context: 钩子上下文
            tool_call: 工具调用请求对象
            tool: 工具实例
            params: 工具参数
            error: 错误对象
        """
        pass

    # ========================================================================
    # 内容后处理钩子
    # ========================================================================

    def finalize_content(self, context: AgentHookContext, content: str | None) -> str | None:
        """
        对最终内容进行后处理。
        
        这是一个同步方法，用于内容的最终格式化。
        
        可用于：
        - 添加或移除特定标记
        - 格式化输出结构
        - 过滤敏感信息
        
        Args:
            context: 钩子上下文
            content: 原始内容
            
        Returns:
            str | None: 处理后的内容
        """
        return content


AgentTurnHookFactory = Callable[[AgentTurnHookContext], AgentHook | None]


class CompositeHook(AgentHook):
    """Fan-out hook that delegates to an ordered list of hooks.

    Error isolation: async methods catch and log per-hook exceptions
    so a faulty custom hook cannot crash the agent loop.
    ``finalize_content`` is a pipeline (no isolation — bugs should surface).
    """

    __slots__ = ("_hooks",)

    def __init__(self, hooks: list[AgentHook]) -> None:
        super().__init__()
        self._hooks = list(hooks)

    def wants_streaming(self) -> bool:
        return any(h.wants_streaming() for h in self._hooks)

    async def _for_each_hook_safe(self, method_name: str, *args: Any, **kwargs: Any) -> None:
        for h in self._hooks:
            if getattr(h, "_reraise", False):
                await getattr(h, method_name)(*args, **kwargs)
                continue

            try:
                await getattr(h, method_name)(*args, **kwargs)
            except Exception:
                logger.exception("AgentHook.{} error in {}", method_name, type(h).__name__)

    async def before_iteration(self, context: AgentHookContext) -> None:
        await self._for_each_hook_safe("before_iteration", context)

    async def before_run(self, context: AgentRunHookContext) -> None:
        await self._for_each_hook_safe("before_run", context)

    async def after_run(self, context: AgentRunHookContext) -> None:
        await self._for_each_hook_safe("after_run", context)

    async def on_error(self, context: AgentRunHookContext) -> None:
        await self._for_each_hook_safe("on_error", context)

    async def on_finally(self, context: AgentRunHookContext) -> None:
        await self._for_each_hook_safe("on_finally", context)

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        await self._for_each_hook_safe("on_stream", context, delta)

    async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
        await self._for_each_hook_safe("on_stream_end", context, resuming=resuming)

    async def on_provider_tool_event(
        self,
        context: AgentHookContext,
        event: dict[str, Any],
    ) -> None:
        await self._for_each_hook_safe("on_provider_tool_event", context, event)

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        await self._for_each_hook_safe("before_execute_tools", context)

    async def before_execute_tool(
        self,
        context: AgentHookContext,
        tool_call: ToolCallRequest,
        tool: Any,
        params: Any,
    ) -> None:
        await self._for_each_hook_safe("before_execute_tool", context, tool_call, tool, params)

    async def after_execute_tool(
        self,
        context: AgentHookContext,
        tool_call: ToolCallRequest,
        tool: Any,
        params: Any,
        result: Any,
    ) -> None:
        await self._for_each_hook_safe(
            "after_execute_tool",
            context,
            tool_call,
            tool,
            params,
            result,
        )

    async def on_execute_tool_error(
        self,
        context: AgentHookContext,
        tool_call: ToolCallRequest,
        tool: Any,
        params: Any,
        error: Any,
    ) -> None:
        await self._for_each_hook_safe(
            "on_execute_tool_error",
            context,
            tool_call,
            tool,
            params,
            error,
        )

    async def emit_reasoning(self, reasoning_content: str | None) -> None:
        await self._for_each_hook_safe("emit_reasoning", reasoning_content)

    async def emit_reasoning_end(self) -> None:
        await self._for_each_hook_safe("emit_reasoning_end")

    async def after_iteration(self, context: AgentHookContext) -> None:
        await self._for_each_hook_safe("after_iteration", context)

    def finalize_content(self, context: AgentHookContext, content: str | None) -> str | None:
        for h in self._hooks:
            content = h.finalize_content(context, content)
        return content


class SDKCaptureHook(AgentHook):
    """Record tool names and the final message list for ``RunResult``.

    The runner mutates ``context.messages`` in place across iterations, so the
    snapshot is refreshed on every ``after_iteration`` call; the last call
    reflects the end-of-turn state the SDK caller cares about.  The run-level
    snapshot is authoritative when available and covers paths without a final
    per-iteration callback.
    """

    def __init__(self) -> None:
        super().__init__()
        self.tools_used: list[str] = []
        self.messages: list[dict[str, Any]] = []
        self.usage: dict[str, int] = {}
        self.stop_reason: str | None = None
        self.error: str | None = None
        self.tool_events: list[dict[str, str]] = []
        self.had_injections: bool = False

    async def after_iteration(self, context: AgentHookContext) -> None:
        for call in context.tool_calls:
            self.tools_used.append(call.name)
        self.messages = list(context.messages)
        self.usage = dict(context.usage)
        self.stop_reason = context.stop_reason
        self.error = context.error
        self.tool_events = list(context.tool_events)

    async def after_run(self, context: AgentRunHookContext) -> None:
        self.tools_used = list(context.tools_used)
        self.messages = list(context.messages)
        self.usage = dict(context.usage)
        self.stop_reason = context.stop_reason
        self.error = context.error
        self.tool_events = list(context.tool_events)
        self.had_injections = context.had_injections
