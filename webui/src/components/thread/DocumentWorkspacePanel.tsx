import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from "react";
import cytoscape from "cytoscape";
import { BookOpen, ChevronDown, ChevronRight, FileCode2, Folder, FolderOpen, PanelRightClose, PanelRightOpen, RefreshCw, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { FilePreviewPanel } from "@/components/FilePreviewPanel";
import { Button } from "@/components/ui/button";
import { fetchKnowledgeProject, fetchWritingRuntime, fetchWorkspaceTree } from "@/lib/api";
import type {
  FileCitation,
  KnowledgeProjectDetailPayload,
  UIFileEdit,
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
  knowledgeProjectId?: string | null;
  onSelectPath: (path: string) => void;
  onResizeStart?: (event: ReactPointerEvent<HTMLButtonElement>) => void;
  onClose: () => void;
  onFileCitation?: (citation: FileCitation) => void;
}

const EXPLORER_DEFAULT_WIDTH = 220;
const EXPLORER_MIN_WIDTH = 170;
const EXPLORER_MAX_WIDTH = 420;

export function DocumentWorkspacePanel({
  sessionKey,
  token,
  selectedPath,
  desktopWidth,
  isClosing = false,
  recentEdits = [],
  writingRuntime,
  knowledgeProjectId = null,
  onSelectPath,
  onResizeStart,
  onClose,
  onFileCitation,
}: DocumentWorkspacePanelProps) {
  const { t } = useTranslation();
  const [entries, setEntries] = useState<WorkspaceTreeNode[]>([]);
  const [treeError, setTreeError] = useState<string | null>(null);
  const [treeLoading, setTreeLoading] = useState(true);
  const [refreshVersion, setRefreshVersion] = useState(0);
  const [runtimeSnapshot, setRuntimeSnapshot] = useState<WritingRuntimePayload | undefined>(writingRuntime);
  const [explorerCollapsed, setExplorerCollapsed] = useState(false);
  const [explorerWidth, setExplorerWidth] = useState(EXPLORER_DEFAULT_WIDTH);
  const [knowledgeDetail, setKnowledgeDetail] = useState<KnowledgeProjectDetailPayload | undefined>();
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
    const maxWidth = Math.min(EXPLORER_MAX_WIDTH, Math.max(EXPLORER_MIN_WIDTH, panelRect.width - 280));
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

  useEffect(() => {
    let cancelled = false;
    if (!knowledgeProjectId) {
      setKnowledgeDetail(undefined);
      return () => {
        cancelled = true;
      };
    }
    void fetchKnowledgeProject(token, sessionKey, knowledgeProjectId)
      .then((payload) => {
        if (!cancelled) setKnowledgeDetail(payload);
      })
      .catch(() => {
        if (!cancelled) setKnowledgeDetail(undefined);
      });
    return () => {
      cancelled = true;
    };
  }, [knowledgeProjectId, refreshVersion, sessionKey, token]);

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
              {knowledgeDetail ? (
                <KnowledgeOverview
                  detail={knowledgeDetail}
                  onSelectPath={onSelectPath}
                />
              ) : null}
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
              <div className="min-h-0 flex-1 overflow-y-auto p-2">
                {treeLoading && entries.length === 0 ? <p className="px-2 py-3 text-xs text-muted-foreground">{t("thread.workspace.loading")}</p> : null}
                {treeError ? <p className="px-2 py-3 text-xs text-destructive">{treeError}</p> : null}
                {!treeLoading && !treeError && entries.length === 0 ? <p className="px-2 py-3 text-xs text-muted-foreground">{t("thread.workspace.empty")}</p> : null}
                <div className="space-y-0.5">
                  {entries.map((entry) => <TreeEntry key={entry.path} node={entry} selectedPath={selectedPath} onSelectPath={onSelectPath} />)}
                </div>
              </div>
            </>
          ) : null}
        </section>
      </div>
    </aside>
  );
}

function KnowledgeOverview({
  detail,
  onSelectPath,
}: {
  detail: KnowledgeProjectDetailPayload;
  onSelectPath: (path: string) => void;
}) {
  const [graphOpen, setGraphOpen] = useState(false);
  return (
    <section className="border-b bg-primary/[0.035] px-2.5 py-2" aria-label="Knowledge workspace">
      <div className="flex items-center gap-1.5">
        <BookOpen className="h-3.5 w-3.5 shrink-0 text-primary/80" aria-hidden />
        <span className="min-w-0 flex-1 truncate text-[11px] font-semibold text-foreground/90">
          {detail.project.title}
        </span>
        <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground">
          {detail.project.phase}
        </span>
      </div>
      <div className="mt-1.5 grid grid-cols-3 gap-1 text-[10px] text-muted-foreground">
        <span>Raw {detail.counts.sources}</span>
        <span>IR {detail.counts.ir_files}</span>
        <span>Pages {detail.counts.pages}</span>
        <span>Entities {detail.counts.entities}</span>
        <span>Relations {detail.counts.relations}</span>
        <span>Reviews {detail.counts.reviews}</span>
      </div>
      {detail.project.task ? (
        <div className="mt-1.5 truncate text-[10px] text-muted-foreground">
          Task {detail.project.task.status} · {detail.project.task.phase} · {detail.project.task.pending_sources} pending
        </div>
      ) : null}
      <div className="mt-1.5 space-y-1">
        <KnowledgePathGroup title="Raw Files" paths={detail.raw_files ?? []} onSelectPath={onSelectPath} />
        <KnowledgePathGroup title="Extracted IR" paths={detail.ir_files ?? []} onSelectPath={onSelectPath} />
        <KnowledgePathGroup title="Wiki Pages" paths={(detail.pages ?? []).map((page) => page.path)} onSelectPath={onSelectPath} />
        <KnowledgePathGroup title="Graph" paths={[detail.paths.graph]} onSelectPath={onSelectPath} />
      </div>
      {detail.graph && detail.graph.nodes.length > 0 ? (
        <>
          <button
            type="button"
            className="mt-1.5 w-full rounded border border-border/60 bg-background/70 px-1.5 py-1 text-left text-[10px] text-foreground/75 hover:bg-accent/60"
            aria-expanded={graphOpen}
            onClick={() => setGraphOpen((value) => !value)}
          >
            {graphOpen ? "Hide graph preview" : `Show graph preview · ${detail.graph.nodes.length} nodes`}
          </button>
          {graphOpen ? <KnowledgeGraphPreview graph={detail.graph} /> : null}
        </>
      ) : null}
    </section>
  );
}

function KnowledgePathGroup({
  title,
  paths,
  onSelectPath,
}: {
  title: string;
  paths: string[];
  onSelectPath: (path: string) => void;
}) {
  if (paths.length === 0) return null;
  const visible = paths.slice(0, 8);
  return (
    <details className="rounded border border-border/50 bg-background/55" open>
      <summary className="cursor-pointer select-none px-1.5 py-1 text-[10px] font-medium text-foreground/75">
        {title} <span className="text-muted-foreground">({paths.length})</span>
      </summary>
      <div className="space-y-0.5 border-t border-border/40 px-1 py-1">
        {visible.map((path) => (
          <button
            key={path}
            type="button"
            className="block w-full truncate rounded px-1 py-0.5 text-left text-[10px] text-muted-foreground hover:bg-accent/60 hover:text-foreground"
            title={path}
            onClick={() => onSelectPath(path)}
          >
            {path}
          </button>
        ))}
        {paths.length > visible.length ? (
          <div className="px-1 pt-0.5 text-[9px] text-muted-foreground">+{paths.length - visible.length} more in file tree</div>
        ) : null}
      </div>
    </details>
  );
}

function KnowledgeGraphPreview({
  graph,
}: {
  graph: NonNullable<KnowledgeProjectDetailPayload["graph"]>;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [graphError, setGraphError] = useState(false);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return undefined;
    setGraphError(false);

    const nodes = graph.nodes
      .filter((node) => node.id.trim())
      .slice(0, 50);
    const nodeIds = new Set(nodes.map((node) => node.id));
    const edges = graph.edges
      .filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target))
      .slice(0, 100);
    const elements: cytoscape.ElementDefinition[] = [
      ...nodes.map((node) => ({
        data: {
          id: node.id,
          label: (node.title || node.id).slice(0, 24),
          type: node.type || "entity",
        },
      })),
      ...edges.map((edge, index) => ({
        data: {
          id: `${edge.source}:${edge.target}:${index}`,
          source: edge.source,
          target: edge.target,
          label: edge.relation || "related",
        },
      })),
    ];

    let instance: cytoscape.Core;
    try {
      instance = cytoscape({
        container,
        elements,
        layout: { name: "cose", animate: false, fit: true, padding: 12 },
        style: [
          {
            selector: "node",
            style: {
              "background-color": "hsl(var(--primary))",
              "background-opacity": 0.8,
              color: "hsl(var(--foreground))",
              label: "data(label)",
              "font-size": 7,
              "text-wrap": "ellipsis",
              "text-max-width": "72px",
              "text-valign": "center",
              "text-halign": "center",
              width: 18,
              height: 18,
            },
          },
          {
            selector: "edge",
            style: {
              width: 1,
              "line-color": "hsl(var(--muted-foreground))",
              "line-opacity": 0.35,
              "target-arrow-color": "hsl(var(--muted-foreground))",
              "target-arrow-shape": "triangle",
              "curve-style": "bezier",
            },
          },
        ],
        userZoomingEnabled: false,
        userPanningEnabled: false,
        boxSelectionEnabled: false,
      });
    } catch {
      setGraphError(true);
      return undefined;
    }

    return () => instance.destroy();
  }, [graph]);

  return (
    <div
      ref={containerRef}
      className="mt-1 h-[150px] overflow-hidden rounded border border-border/60 bg-background/75"
      aria-label="Knowledge graph preview"
      data-knowledge-graph-canvas
    >
      {graph.nodes.length === 0 ? <span className="flex h-full items-center justify-center text-[10px] text-muted-foreground">No graph data</span> : null}
      {graphError ? <span className="flex h-full items-center justify-center px-2 text-center text-[10px] text-muted-foreground">Graph preview unavailable</span> : null}
    </div>
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
