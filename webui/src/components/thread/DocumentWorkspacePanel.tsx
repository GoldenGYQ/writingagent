import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from "react";
import { ChevronDown, ChevronRight, FileCode2, Folder, FolderOpen, PanelRightClose, PanelRightOpen, RefreshCw, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { FilePreviewPanel } from "@/components/FilePreviewPanel";
import { Button } from "@/components/ui/button";
import { fetchWritingRuntime, fetchWorkspaceTree } from "@/lib/api";
import type {
  FileCitation,
  UIFileEdit,
  WritingChangeSetResult,
  WorkspaceTreeNode,
  WritingRuntimePayload,
} from "@/lib/types";
import { cn } from "@/lib/utils";

interface DocumentWorkspacePanelProps {
  sessionKey: string;
  token: string;
  selectedPath: string | null;
  desktopWidth: number;
  isClosing?: boolean;
  recentEdits?: UIFileEdit[];
  writingRuntime?: WritingRuntimePayload;
  onSelectPath: (path: string) => void;
  onResizeStart?: (event: ReactPointerEvent<HTMLButtonElement>) => void;
  onClose: () => void;
  onFileCitation?: (citation: FileCitation) => void;
  onSaveContent?: (request: { path: string; content: string; reason?: string }) => Promise<WritingChangeSetResult>;
}

const EXPLORER_DEFAULT_WIDTH = 360;
const EXPLORER_MIN_WIDTH = 240;
const EXPLORER_MAX_WIDTH = 600;

export function DocumentWorkspacePanel({
  sessionKey,
  token,
  selectedPath,
  desktopWidth,
  isClosing = false,
  recentEdits = [],
  writingRuntime,
  onSelectPath,
  onResizeStart,
  onClose,
  onFileCitation,
  onSaveContent,
}: DocumentWorkspacePanelProps) {
  const { t } = useTranslation();
  const [entries, setEntries] = useState<WorkspaceTreeNode[]>([]);
  const [treeError, setTreeError] = useState<string | null>(null);
  const [treeLoading, setTreeLoading] = useState(true);
  const [refreshVersion, setRefreshVersion] = useState(0);
  const [runtimeSnapshot, setRuntimeSnapshot] = useState<WritingRuntimePayload | undefined>(writingRuntime);
  const [explorerCollapsed, setExplorerCollapsed] = useState(false);
  const [explorerWidth, setExplorerWidth] = useState(EXPLORER_DEFAULT_WIDTH);
  const explorerWidthRef = useRef(EXPLORER_DEFAULT_WIDTH);

  const handleExplorerResizeStart = useCallback((event: ReactPointerEvent<HTMLButtonElement>) => {
    event.preventDefault();
    event.stopPropagation();
    const panel = event.currentTarget.closest<HTMLElement>("[data-document-workspace]");
    const panelRect = panel?.getBoundingClientRect();
    if (!panelRect) return;

    const originalCursor = document.body.style.cursor;
    const originalUserSelect = document.body.style.userSelect;
    let frame: number | null = null;
    let nextWidth = explorerWidthRef.current;
    const maxWidth = Math.min(EXPLORER_MAX_WIDTH, Math.max(EXPLORER_MIN_WIDTH, panelRect.width - 360));
    const applyWidth = (clientX: number) => {
      nextWidth = Math.min(maxWidth, Math.max(EXPLORER_MIN_WIDTH, panelRect.right - clientX));
      explorerWidthRef.current = nextWidth;
      if (frame !== null) return;
      frame = window.requestAnimationFrame(() => {
        frame = null;
        panel?.style.setProperty("--workspace-explorer-width", `${nextWidth}px`);
      });
    };
    const handleMove = (moveEvent: PointerEvent) => {
      moveEvent.preventDefault();
      applyWidth(moveEvent.clientX);
    };
    const handleEnd = () => {
      if (frame !== null) {
        window.cancelAnimationFrame(frame);
        frame = null;
      }
      setExplorerWidth(nextWidth);
      document.body.style.cursor = originalCursor;
      document.body.style.userSelect = originalUserSelect;
      window.removeEventListener("pointermove", handleMove);
      window.removeEventListener("pointerup", handleEnd);
      window.removeEventListener("pointercancel", handleEnd);
    };

    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    applyWidth(event.clientX);
    window.addEventListener("pointermove", handleMove);
    window.addEventListener("pointerup", handleEnd);
    window.addEventListener("pointercancel", handleEnd);
  }, []);

  const loadTree = () => {
    setTreeLoading(true);
    void fetchWorkspaceTree(token, sessionKey, { depth: 4, limit: 400 })
      .then((payload) => {
        setEntries(payload.entries);
        setTreeError(null);
      })
      .catch((error) => {
        setTreeError(error instanceof Error ? error.message : t("thread.workspace.treeError"));
      })
      .finally(() => setTreeLoading(false));
  };

  useEffect(() => {
    loadTree();
    // The session key is the security boundary; refresh is user initiated.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionKey, token, refreshVersion]);

  useEffect(() => {
    if (writingRuntime) setRuntimeSnapshot(writingRuntime);
  }, [writingRuntime]);

  useEffect(() => {
    let cancelled = false;
    void fetchWritingRuntime(token, sessionKey)
      .then((payload) => {
        if (!cancelled) setRuntimeSnapshot(payload);
      })
      .catch(() => {
        if (!cancelled) setRuntimeSnapshot(undefined);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionKey, token, refreshVersion, recentEdits.length]);

  const recent = useMemo(() => {
    const seen = new Set<string>();
    return recentEdits.filter((edit) => {
      if (!edit.path || seen.has(edit.path)) return false;
      seen.add(edit.path);
      return true;
    }).slice(0, 8);
  }, [recentEdits]);

  return (
    <aside
      className={cn(
        "relative flex min-h-0 w-[min(100vw,var(--file-preview-width))] shrink-0 flex-col overflow-hidden border-l bg-background/95 shadow-[-10px_0_30px_-24px_rgba(0,0,0,0.55)] transition-[width,transform,opacity] duration-300 ease-out md:w-[var(--file-preview-width)] motion-reduce:transition-none",
        isClosing && "translate-x-3 opacity-0",
      )}
      style={{
        "--file-preview-width": `${desktopWidth}px`,
        "--file-preview-slot-width": `${desktopWidth}px`,
        "--workspace-explorer-width": `${explorerWidth}px`,
      } as CSSProperties}
      data-file-preview-panel
      data-document-workspace
      data-testid="document-workspace-panel"
      aria-label={t("thread.workspace.title")}
    >
      {onResizeStart ? (
        <button
          type="button"
          className="absolute inset-y-0 left-0 z-20 hidden w-1 cursor-col-resize bg-transparent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring md:block"
          aria-label={t("thread.workspace.resize")}
          onPointerDown={onResizeStart}
        />
      ) : null}
      <div className="flex min-h-0 flex-1">
        <section className="min-w-0 flex-1" aria-label={t("thread.workspace.preview")}>
          {selectedPath ? (
            <FilePreviewPanel
              sessionKey={sessionKey}
              path={selectedPath}
              token={token}
              embedded
              refreshKey={recentEdits.length}
              onClose={() => onSelectPath("")}
              onFileCitation={onFileCitation}
              onOpenFilePreview={onSelectPath}
              onSaveContent={onSaveContent}
            />
          ) : (
            <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center text-muted-foreground">
              <FileCode2 className="h-8 w-8 opacity-35" />
              <p className="text-sm">{t("thread.workspace.selectFile")}</p>
            </div>
          )}
        </section>
        <section
          className={cn(
            "relative flex min-h-0 shrink-0 flex-col border-l bg-muted/15 transition-[width] duration-200 motion-reduce:transition-none",
            explorerCollapsed ? "w-11" : "w-[var(--workspace-explorer-width)]",
          )}
          aria-label={t("thread.workspace.explorer")}
        >
          {runtimeSnapshot?.active ? (
            <div className="border-b bg-primary/[0.04] px-3 py-2 text-[11px]">
              <div className="truncate font-semibold text-foreground/90">
                {String(runtimeSnapshot.document?.title ?? runtimeSnapshot.project?.title ?? "Writing project")}
              </div>
              <div className="mt-1 flex items-center gap-2 text-muted-foreground">
                {runtimeSnapshot.chapter?.title ? <span className="truncate">{String(runtimeSnapshot.chapter.title)}</span> : null}
                {runtimeSnapshot.context?.revision_id ? <span className="shrink-0">{runtimeSnapshot.context.revision_id}</span> : null}
                {(runtimeSnapshot.pending_changesets?.length ?? 0) > 0 ? (
                  <span className="shrink-0 text-amber-600">{runtimeSnapshot.pending_changesets?.length} pending</span>
                ) : null}
              </div>
            </div>
          ) : null}
          {!explorerCollapsed ? (
            <button
              type="button"
              className="group absolute inset-y-0 left-0 z-20 hidden w-2 -translate-x-1/2 cursor-col-resize items-stretch justify-center touch-none focus-visible:outline-none md:flex"
              aria-label={t("thread.workspace.explorerResize", { defaultValue: "Resize file tree" })}
              onPointerDown={handleExplorerResizeStart}
            >
              <span aria-hidden className="h-full w-px bg-foreground/25 opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:bg-ring group-focus-visible:opacity-100" />
            </button>
          ) : null}
          <div className={cn("flex h-11 shrink-0 items-center border-b", explorerCollapsed ? "justify-center px-1" : "justify-between px-3")}>
            {!explorerCollapsed ? (
              <span className="truncate text-[12px] font-semibold tracking-wide text-foreground/85">{t("thread.workspace.explorer")}</span>
            ) : null}
            <div className="flex items-center gap-0.5">
              {!explorerCollapsed ? (
                <Button variant="ghost" size="icon" className="h-7 w-7" aria-label={t("thread.workspace.refresh")} onClick={() => setRefreshVersion((value) => value + 1)}>
                  <RefreshCw className={cn("h-3.5 w-3.5", treeLoading && "animate-spin motion-reduce:animate-none")} />
                </Button>
              ) : null}
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                aria-label={explorerCollapsed
                  ? t("thread.workspace.expandExplorer", { defaultValue: "Expand file tree" })
                  : t("thread.workspace.collapseExplorer", { defaultValue: "Collapse file tree" })}
                aria-expanded={!explorerCollapsed}
                onClick={() => setExplorerCollapsed((value) => !value)}
              >
                {explorerCollapsed ? <PanelRightOpen className="h-3.5 w-3.5" /> : <PanelRightClose className="h-3.5 w-3.5" />}
              </Button>
              {!explorerCollapsed ? (
                <Button variant="ghost" size="icon" className="h-7 w-7" aria-label={t("thread.workspace.close")} onClick={onClose}>
                  <X className="h-3.5 w-3.5" />
                </Button>
              ) : null}
            </div>
          </div>
          {!explorerCollapsed ? (
            <>
              <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
                {recent.length > 0 ? (
                  <div className="border-b px-2 py-2">
                    <div className="px-1 pb-1 text-[10px] font-medium uppercase tracking-[0.14em] text-muted-foreground">{t("thread.workspace.recent")}</div>
                    <div className="space-y-0.5">
                      {recent.map((edit) => (
                        <button
                          key={`${edit.call_id}:${edit.path}`}
                          type="button"
                          className={cn("flex w-full items-center gap-1.5 rounded-md px-1.5 py-1 text-left text-[11px] hover:bg-accent/45 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", selectedPath === edit.path && "bg-accent/55")}
                          onClick={() => onSelectPath(edit.path)}
                        >
                          <FileCode2 className="h-3.5 w-3.5 shrink-0 text-primary/75" />
                          <span className="min-w-0 flex-1 truncate">{edit.path}</span>
                          <span className="shrink-0 tabular-nums text-[10px] text-muted-foreground">+{edit.added} -{edit.deleted}</span>
                        </button>
                      ))}
                    </div>
                  </div>
                ) : null}
                <div className="p-2">
                  {treeLoading && entries.length === 0 ? <p className="px-2 py-3 text-xs text-muted-foreground">{t("thread.workspace.loading")}</p> : null}
                  {treeError ? <p className="px-2 py-3 text-xs text-destructive">{treeError}</p> : null}
                  {!treeLoading && !treeError && entries.length === 0 ? <p className="px-2 py-3 text-xs text-muted-foreground">{t("thread.workspace.empty")}</p> : null}
                  <div className="space-y-0.5">
                    {entries.map((entry) => <TreeEntry key={entry.path} node={entry} selectedPath={selectedPath} onSelectPath={onSelectPath} />)}
                  </div>
                </div>
              </div>
            </>
          ) : null}
        </section>
      </div>
    </aside>
  );
}

function TreeEntry({ node, selectedPath, onSelectPath, depth = 0 }: { node: WorkspaceTreeNode; selectedPath: string | null; onSelectPath: (path: string) => void; depth?: number }) {
  const [open, setOpen] = useState(depth < 1);
  const directory = node.kind === "directory";
  return (
    <div>
      <div className={cn("flex items-center rounded-md text-[12px] hover:bg-accent/45", selectedPath === node.path && "bg-accent/60")} style={{ paddingLeft: `${depth * 12 + 2}px` }}>
        {directory ? (
          <button type="button" className="flex h-7 w-6 items-center justify-center text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-label={open ? `Collapse ${node.name}` : `Expand ${node.name}`} onClick={() => setOpen((value) => !value)}>
            {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
          </button>
        ) : <span className="w-6" />}
        <button type="button" className="flex min-w-0 flex-1 items-center gap-1.5 py-1 pr-1 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => directory ? setOpen((value) => !value) : onSelectPath(node.path)}>
          {directory ? (open ? <FolderOpen className="h-3.5 w-3.5 shrink-0 text-amber-500/80" /> : <Folder className="h-3.5 w-3.5 shrink-0 text-amber-500/80" />) : <FileCode2 className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />}
          <span className="min-w-0 truncate">{node.name}</span>
        </button>
      </div>
      {directory && open && node.children?.map((child) => <TreeEntry key={child.path} node={child} selectedPath={selectedPath} onSelectPath={onSelectPath} depth={depth + 1} />)}
    </div>
  );
}
