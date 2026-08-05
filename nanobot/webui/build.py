"""
Helpers for keeping the bundled WebUI build in sync with source checkouts.

这个模块负责管理和同步WebUI前端构建产物与源代码的版本。

核心问题：
- WebUI前端代码在 `webui/` 目录下开发
- 生产环境使用打包后的静态文件（在 `nanobot/web/dist/`）
- 开发过程中前端代码频繁更新，需要确保打包文件与源码同步

主要功能：
1. 检查WebUI源码和打包产物的新鲜度
2. 根据配置模式自动或提示地重新构建WebUI
3. 支持多种包管理器（bun, npm）
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

# ============================================================================
# 类型定义
# ============================================================================

# 构建模式：控制何时以及如何触发WebUI构建
BuildMode = Literal[
    "auto",    # 自动构建（无需用户交互）
    "prompt",  # 提示用户确认是否构建
    "warn",    # 仅警告，不构建
    "skip",    # 完全跳过构建检查
]


# ============================================================================
# 常量定义
# ============================================================================

# 顶层关键文件列表 - 这些文件的变更会触发重新构建
_SOURCE_TOP_LEVEL_FILES = (
    "index.html",           # 入口HTML
    "package.json",         # npm依赖配置
    "bun.lock",             # Bun锁文件
    "package-lock.json",    # npm锁文件
    "pnpm-lock.yaml",       # pnpm锁文件
    "yarn.lock",            # Yarn锁文件
    "vite.config.ts",       # Vite配置（TypeScript）
    "vite.config.js",       # Vite配置（JavaScript）
    "tailwind.config.ts",   # Tailwind CSS配置（TypeScript）
    "tailwind.config.js",   # Tailwind CSS配置（JavaScript）
    "postcss.config.ts",    # PostCSS配置（TypeScript）
    "postcss.config.js",    # PostCSS配置（JavaScript）
    "tsconfig.json",        # TypeScript配置
    "tsconfig.build.json",  # TypeScript构建配置
    "components.json",      # 组件配置（shadcn/ui等）
)

# WebUI源代码目录（这些目录中的所有文件变更都会触发重新构建）
_SOURCE_DIRS = ("src", "public")


# ============================================================================
# 自定义异常
# ============================================================================

class WebUIBuildError(RuntimeError):
    """当本地WebUI打包失败时抛出。"""
    pass


# ============================================================================
# 数据类：WebUI打包状态
# ============================================================================

@dataclass(frozen=True)
class WebUIBundleStatus:
    """
    WebUI源码打包状态信息。
    
    用于描述当前WebUI源码和打包产物的新鲜度关系。
    """
    
    source_dir: Path          # WebUI源码目录
    dist_dir: Path            # 打包产物目录
    index_html: Path          # 入口HTML文件路径
    
    source_available: bool    # 源码是否可用
    dist_available: bool      # 打包产物是否可用
    
    stale: bool               # 打包产物是否过时（需要重新构建）
    reason: str               # 状态原因描述
    
    # 最新变更的源文件信息（用于诊断）
    newest_source: Path | None = None
    newest_source_mtime_ns: int | None = None  # 最新文件修改时间（纳秒）
    dist_mtime_ns: int | None = None           # 打包产物修改时间（纳秒）

    @property
    def needs_build(self) -> bool:
        """
        判断是否需要构建。
        
        条件：源码可用 且 打包产物过时
        """
        return self.source_available and self.stale


# ============================================================================
# 路径工具函数
# ============================================================================

def default_project_root() -> Path:
    """
    返回源码仓库根目录。
    
    通过当前文件路径向上两级获取项目根目录。
    假设文件位置：nanobot/webui/build_utils.py
    项目根目录：../../ （上两级）
    
    Returns:
        项目根目录路径
    """
    return Path(__file__).resolve().parents[2]


def default_webui_source_dir(project_root: Path | None = None) -> Path:
    """
    返回WebUI源码目录。
    
    约定：在项目根目录下的 webui/ 目录。
    
    Args:
        project_root: 项目根目录，默认自动检测
        
    Returns:
        WebUI源码目录路径
    """
    root = project_root or default_project_root()
    return root / "webui"


def default_webui_dist_dir(project_root: Path | None = None) -> Path:
    """
    返回WebUI打包产物目录。
    
    查找顺序：
    1. 如果已安装 nanobot.web 包，使用包内的 dist/ 目录
    2. 否则使用项目根目录下的 nanobot/web/dist/（开发模式）
    
    Args:
        project_root: 项目根目录，默认自动检测
        
    Returns:
        WebUI打包产物目录路径
    """
    try:
        # 尝试从已安装的包中获取路径
        import nanobot.web as web_pkg  # type: ignore[import-not-found]
    except ImportError:
        # 开发模式：使用本地目录
        root = project_root or default_project_root()
        return root / "nanobot" / "web" / "dist"
    
    # 生产模式：使用包内的dist目录
    return Path(web_pkg.__file__).resolve().parent / "dist"


# ============================================================================
# 源码文件扫描函数
# ============================================================================

def iter_webui_source_files(source_dir: Path) -> list[Path]:
    """
    返回所有会影响WebUI打包的源文件列表。
    
    扫描策略：
    1. 顶层关键文件（_SOURCE_TOP_LEVEL_FILES）
    2. 源代码目录（_SOURCE_DIRS）中的所有文件
    3. 渠道（channels）的webui目录（扩展支持）
    
    这些文件的修改时间用于判断打包产物是否过时。
    
    Args:
        source_dir: WebUI源码目录
        
    Returns:
        源文件路径列表
    """
    files: list[Path] = []
    
    # 1. 添加顶层关键文件
    for name in _SOURCE_TOP_LEVEL_FILES:
        candidate = source_dir / name
        if candidate.is_file():
            files.append(candidate)
    
    # 2. 添加源代码目录中的所有文件
    for dirname in _SOURCE_DIRS:
        root = source_dir / dirname
        if not root.is_dir():
            continue
        # 递归遍历所有文件
        files.extend(path for path in root.rglob("*") if path.is_file())
    
    # 3. 添加渠道的webui目录（支持插件式WebUI）
    # 例如：nanobot/channels/*/webui/
    channel_root = source_dir.parent / "nanobot" / "channels"
    if channel_root.is_dir():
        for channel_webui in channel_root.glob("*/webui"):
            files.extend(path for path in channel_webui.rglob("*") if path.is_file())
    
    return files


# ============================================================================
# 核心：检查WebUI打包状态
# ============================================================================

def inspect_webui_bundle(
    *,
    source_dir: Path | None = None,
    dist_dir: Path | None = None,
) -> WebUIBundleStatus:
    """
    检查WebUI打包产物是否与源码保持同步。
    
    检查逻辑：
    1. 如果源码目录不存在 package.json → 认为没有源码
    2. 如果打包产物不存在 index.html → 需要构建
    3. 找到源码中修改时间最新的文件
    4. 如果最新源码文件比打包产物更新 → 需要构建
    5. 否则 → 打包产物是最新的
    
    Args:
        source_dir: WebUI源码目录，默认自动检测
        dist_dir: WebUI打包产物目录，默认自动检测
        
    Returns:
        WebUIBundleStatus: 打包状态信息
    """
    
    # 解析目录路径
    resolved_source = source_dir or default_webui_source_dir()
    resolved_dist = dist_dir or default_webui_dist_dir()
    index_html = resolved_dist / "index.html"
    
    # ---------- 情况1：源码不存在 ----------
    # 检查 package.json 是否存在（Node.js项目的标志文件）
    if not (resolved_source / "package.json").is_file():
        return WebUIBundleStatus(
            source_dir=resolved_source,
            dist_dir=resolved_dist,
            index_html=index_html,
            source_available=False,
            dist_available=index_html.is_file(),
            stale=False,
            reason="no_source",  # 没有源码
        )
    
    # ---------- 情况2：打包产物不存在 ----------
    if not index_html.is_file():
        return WebUIBundleStatus(
            source_dir=resolved_source,
            dist_dir=resolved_dist,
            index_html=index_html,
            source_available=True,
            dist_available=False,
            stale=True,  # 需要构建
            reason="missing_dist",  # 缺少打包产物
        )
    
    # ---------- 情况3：比较修改时间 ----------
    # 获取打包产物的修改时间（使用 index.html 作为代表）
    dist_mtime_ns = index_html.stat().st_mtime_ns
    
    # 扫描所有源文件，找到最新修改的文件
    newest_source: Path | None = None
    newest_source_mtime_ns: int | None = None
    
    for candidate in iter_webui_source_files(resolved_source):
        try:
            mtime_ns = candidate.stat().st_mtime_ns
        except OSError:
            # 无法读取文件信息（权限问题等），跳过
            continue
        
        # 更新最新文件
        if newest_source_mtime_ns is None or mtime_ns > newest_source_mtime_ns:
            newest_source = candidate
            newest_source_mtime_ns = mtime_ns
    
    # ---------- 情况4：源码比打包产物更新 ----------
    if newest_source_mtime_ns is not None and newest_source_mtime_ns > dist_mtime_ns:
        return WebUIBundleStatus(
            source_dir=resolved_source,
            dist_dir=resolved_dist,
            index_html=index_html,
            source_available=True,
            dist_available=True,
            stale=True,  # 需要构建
            reason="source_newer",  # 源码更新
            newest_source=newest_source,
            newest_source_mtime_ns=newest_source_mtime_ns,
            dist_mtime_ns=dist_mtime_ns,
        )
    
    # ---------- 情况5：打包产物是最新的 ----------
    return WebUIBundleStatus(
        source_dir=resolved_source,
        dist_dir=resolved_dist,
        index_html=index_html,
        source_available=True,
        dist_available=True,
        stale=False,  # 不需要构建
        reason="fresh",  # 新鲜
        newest_source=newest_source,
        newest_source_mtime_ns=newest_source_mtime_ns,
        dist_mtime_ns=dist_mtime_ns,
    )


# ============================================================================
# 状态描述函数
# ============================================================================

def describe_webui_bundle_status(status: WebUIBundleStatus) -> str:
    """
    生成面向用户的简短状态描述信息。
    
    Args:
        status: WebUI打包状态
        
    Returns:
        人类可读的状态描述
    """
    if status.reason == "missing_dist":
        return "Bundled WebUI build is missing."
    
    if status.reason == "source_newer":
        changed = _display_source_path(status)
        return f"WebUI source is newer than the bundled build ({changed})."
    
    if status.reason == "fresh":
        return "Bundled WebUI build is up to date."
    
    return "WebUI source tree was not found; using the bundled build."


# ============================================================================
# 核心：构建WebUI打包
# ============================================================================

def build_webui_bundle(
    *,
    source_dir: Path | None = None,
    dist_dir: Path | None = None,
    runner: str | None = None,
    subprocess_run: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
    output: Callable[[str], None] | None = None,
) -> WebUIBundleStatus:
    """
    安装前端依赖并构建WebUI打包产物。
    
    构建流程：
    1. 选择合适的包管理器（bun 或 npm）
    2. 运行 `[runner] install` 安装依赖
    3. 运行 `[runner] run build` 构建打包
    
    Args:
        source_dir: WebUI源码目录
        dist_dir: WebUI打包产物目录
        runner: 包管理器路径（默认自动检测）
        subprocess_run: subprocess.run的替代实现（用于测试）
        output: 输出回调函数（用于显示构建日志）
        
    Returns:
        WebUIBundleStatus: 构建后的打包状态
        
    Raises:
        WebUIBuildError: 构建失败时抛出
    """
    resolved_source = source_dir or default_webui_source_dir()
    
    # 1. 选择包管理器
    command_runner = runner or pick_webui_build_runner()
    if command_runner is None:
        raise WebUIBuildError(
            "neither `bun` nor `npm` is available on PATH; install one or run "
            "`cd webui && bun run build` manually"
        )
    
    # 2. 输出构建信息
    _emit(output, f"Building bundled WebUI with `{command_runner}`...")
    
    # 3. 安装依赖
    _run_frontend_command(
        [command_runner, "install"],
        cwd=resolved_source,
        subprocess_run=subprocess_run,
    )
    
    # 4. 执行构建
    _run_frontend_command(
        [command_runner, "run", "build"],
        cwd=resolved_source,
        subprocess_run=subprocess_run,
    )
    
    # 5. 检查构建结果
    return inspect_webui_bundle(source_dir=resolved_source, dist_dir=dist_dir)


# ============================================================================
# 核心：确保WebUI打包（根据模式）
# ============================================================================

def ensure_webui_bundle(
    *,
    mode: BuildMode,
    source_dir: Path | None = None,
    dist_dir: Path | None = None,
    confirm: Callable[[str], bool] | None = None,
    output: Callable[[str], None] | None = None,
    runner: str | None = None,
    environ: Mapping[str, str] | None = None,
    subprocess_run: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
) -> WebUIBundleStatus:
    """
    根据指定的模式确保WebUI打包是最新的。
    
    这是对外的主要接口，根据配置的构建模式决定如何处理过时的打包。
    
    模式说明：
    - auto: 自动构建，无需用户交互
    - prompt: 提示用户确认是否构建
    - warn: 仅警告，不构建
    - skip: 完全跳过检查和构建
    
    环境变量：
    - NANOBOT_SKIP_WEBUI_BUILD=1: 强制跳过构建（覆盖其他模式）
    
    Args:
        mode: 构建模式
        source_dir: WebUI源码目录
        dist_dir: WebUI打包产物目录
        confirm: 用户确认回调函数（用于prompt模式）
        output: 输出回调函数
        runner: 包管理器路径
        environ: 环境变量（默认使用os.environ）
        subprocess_run: subprocess.run的替代实现
        
    Returns:
        WebUIBundleStatus: 最终打包状态
        
    Raises:
        WebUIBuildError: 构建失败时抛出
    """
    env = environ or os.environ
    
    # 1. 检查当前打包状态
    status = inspect_webui_bundle(source_dir=source_dir, dist_dir=dist_dir)
    
    # 2. 如果不需要构建，直接返回
    if not status.needs_build:
        return status
    
    # 3. 获取状态描述
    detail = describe_webui_bundle_status(status)
    
    # 4. 检查环境变量（强制跳过）
    if env.get("NANOBOT_SKIP_WEBUI_BUILD") == "1" or mode == "skip":
        _emit(output, f"Warning: {detail} Skipping WebUI build.")
        return status
    
    # 5. warn模式：仅警告
    if mode == "warn":
        _emit(
            output,
            f"Warning: {detail} Run `cd {status.source_dir} && bun run build` "
            "to refresh it.",
        )
        return status
    
    # 6. prompt模式：询问用户
    if mode == "prompt":
        if confirm is None:
            _emit(output, f"Warning: {detail} No interactive confirmation is available.")
            return status
        
        message = "Build WebUI now? This runs `cd webui && bun run build`."
        if not confirm(message):
            _emit(output, "Continuing with the existing bundled WebUI build.")
            return status
    
    # 7. auto模式或用户确认：执行构建
    try:
        return build_webui_bundle(
            source_dir=status.source_dir,
            dist_dir=status.dist_dir,
            runner=runner,
            subprocess_run=subprocess_run,
            output=output,
        )
    except WebUIBuildError as exc:
        # 构建失败，抛出更详细的错误信息
        raise WebUIBuildError(f"{detail} {exc}") from exc


# ============================================================================
# 辅助函数
# ============================================================================

def pick_webui_build_runner() -> str | None:
    """
    选择合适的包管理器。
    
    优先级：
    1. bun（更快、更现代）
    2. npm（最广泛使用）
    
    Returns:
        包管理器可执行文件路径，如果都不可用则返回None
    """
    for candidate in ("bun", "npm"):
        if executable := shutil.which(candidate):
            return executable
    return None


def _run_frontend_command(
    command: list[str],
    *,
    cwd: Path,
    subprocess_run: Callable[..., subprocess.CompletedProcess[Any]],
) -> None:
    """
    执行前端构建命令的内部函数。
    
    Args:
        command: 命令及其参数列表
        cwd: 工作目录
        subprocess_run: subprocess.run的替代实现
        
    Raises:
        WebUIBuildError: 命令执行失败时抛出
    """
    try:
        # 执行命令，check=True表示失败时抛出异常
        subprocess_run(command, cwd=cwd, check=True)
    except subprocess.CalledProcessError as exc:
        # 命令返回非零退出码
        raise WebUIBuildError(
            f"command failed ({exc.returncode}): {' '.join(command)}"
        ) from exc
    except OSError as exc:
        # 系统调用失败（如文件不存在）
        raise WebUIBuildError(f"command failed: {' '.join(command)} ({exc})") from exc


def _display_source_path(status: WebUIBundleStatus) -> str:
    """
    生成源文件路径的显示字符串。
    
    尝试显示相对于 source_dir 的路径，如果失败则显示完整路径。
    
    Args:
        status: WebUI打包状态
        
    Returns:
        格式化的路径字符串
    """
    if status.newest_source is None:
        return "source files changed"
    
    # 尝试转换为相对路径
    with suppress(ValueError):
        return str(status.newest_source.relative_to(status.source_dir))
    
    # 失败时使用完整路径
    return str(status.newest_source)


def _emit(output: Callable[[str], None] | None, message: str) -> None:
    """
    输出消息的内部函数。
    
    Args:
        output: 输出回调函数
        message: 要输出的消息
    """
    if output is not None:
        output(message)

