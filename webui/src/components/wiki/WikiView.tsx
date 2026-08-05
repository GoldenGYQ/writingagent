import { useEffect, useMemo, useRef, useState } from "react";
import cytoscape from "cytoscape";
import {
  BookOpen,
  ChevronDown,
  ChevronRight,
  FileText,
  Folder,
  FolderOpen,
  FileCode2,
  GitBranch,
  Loader2,
  Network,
  PanelRightOpen,
  RotateCcw,
  RefreshCw,
  Search,
  SlidersHorizontal,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { FilePreviewPanel } from "@/components/FilePreviewPanel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fetchKnowledgeProject, fetchKnowledgeProjects, fetchWorkspaceTree } from "@/lib/api";
import type { KnowledgeProjectDetailPayload, KnowledgeProjectSummary, WorkspaceTreeNode, WritingChangeSetResult } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

type WikiViewMode = "preview" | "graph";
type PageBrowserMode = "knowledge" | "files";

const KNOWLEDGE_GROUPS = [
  { key: "overview", label: "概览", icon: BookOpen, color: "text-amber-500" },
  { key: "entity", label: "实体", icon: Network, color: "text-sky-500" },
  { key: "concept", label: "概念", icon: BookOpen, color: "text-violet-500" },
  { key: "source", label: "资料", icon: FileText, color: "text-orange-500" },
  { key: "comparison", label: "综合", icon: GitBranch, color: "text-rose-500" },
  { key: "query", label: "查询", icon: Search, color: "text-emerald-500" },
  { key: "raw", label: "原始资料", icon: Folder, color: "text-orange-500" },
] as const;

interface WikiViewProps {
  sessionKey: string | null;
  onBackToChat: () => void;
}

export function WikiView({ sessionKey, onBackToChat }: WikiViewProps) {
  const { t } = useTranslation();
  const { client, getToken } = useClient();
  const [projects, setProjects] = useState<KnowledgeProjectSummary[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [detail, setDetail] = useState<KnowledgeProjectDetailPayload | null>(null);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [browserMode, setBrowserMode] = useState<PageBrowserMode>("knowledge");
  const [viewMode, setViewMode] = useState<WikiViewMode>("preview");
  const [workspaceEntries, setWorkspaceEntries] = useState<WorkspaceTreeNode[]>([]);
  const [workspaceLoading, setWorkspaceLoading] = useState(false);
  const [workspaceError, setWorkspaceError] = useState<string | null>(null);
  const [loadingProjects, setLoadingProjects] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [refreshVersion, setRefreshVersion] = useState(0);
  const saveContent = useMemo(
    () => (request: { path: string; content: string; reason?: string }): Promise<WritingChangeSetResult> => {
      if (!sessionKey) return Promise.reject(new Error("No active workspace session."));
      return client.proposeWritingChangeSet(sessionKey.replace(/^websocket:/, ""), request);
    },
    [client, sessionKey],
  );

  useEffect(() => {
    if (!sessionKey) {
      setProjects([]);
      setSelectedProjectId(null);
      setDetail(null);
      setSelectedPath(null);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoadingProjects(true);
    setError(null);
    void fetchKnowledgeProjects(getToken(), sessionKey)
      .then((payload) => {
        if (cancelled) return;
        setProjects(payload.projects ?? []);
        setSelectedProjectId((current) => {
          if (current && payload.projects.some((project) => project.id === current)) return current;
          return payload.projects[0]?.id ?? null;
        });
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setProjects([]);
          setError(reason instanceof Error ? reason.message : t("wiki.loadFailed", { defaultValue: "Could not load Wiki projects." }));
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingProjects(false);
      });
    return () => {
      cancelled = true;
    };
  }, [getToken, refreshVersion, sessionKey, t]);

  useEffect(() => {
    if (!sessionKey || !selectedProjectId) {
      setDetail(null);
      setSelectedPath(null);
      return;
    }
    let cancelled = false;
    setLoadingDetail(true);
    setDetailError(null);
    void fetchKnowledgeProject(getToken(), sessionKey, selectedProjectId)
      .then((payload) => {
        if (cancelled) return;
        setDetail(payload);
        setSelectedPath((current) => {
          if (current && payload.pages.some((page) => page.path === current)) return current;
          return payload.pages[0]?.path ?? null;
        });
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setDetail(null);
          setSelectedPath(null);
          setDetailError(reason instanceof Error ? reason.message : t("wiki.detailFailed", { defaultValue: "Could not load this Wiki project." }));
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingDetail(false);
      });
    return () => {
      cancelled = true;
    };
  }, [getToken, refreshVersion, selectedProjectId, sessionKey, t]);

  useEffect(() => {
    if (!sessionKey || browserMode !== "files") return;
    let cancelled = false;
    setWorkspaceLoading(true);
    setWorkspaceError(null);
    void fetchWorkspaceTree(getToken(), sessionKey, { depth: 6, limit: 600 })
      .then((payload) => {
        if (!cancelled) setWorkspaceEntries(payload.entries ?? []);
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setWorkspaceEntries([]);
          setWorkspaceError(reason instanceof Error ? reason.message : "Could not load workspace files.");
        }
      })
      .finally(() => {
        if (!cancelled) setWorkspaceLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [browserMode, getToken, refreshVersion, sessionKey]);

  const selectedProject = projects.find((project) => project.id === selectedProjectId) ?? null;
  const openWikiReference = (reference: string) => {
    if (!detail) return;
    const targetPath = resolveGraphPagePath(reference, reference, detail.pages);
    if (targetPath) {
      setSelectedPath(targetPath);
      setViewMode("preview");
    }
  };
  const filteredPages = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return (detail?.pages ?? []).filter((page) => {
      if (!needle) return true;
      return [page.title, page.slug, page.path, ...(page.tags ?? [])]
        .filter(Boolean)
        .some((value) => String(value).toLocaleLowerCase().includes(needle));
    });
  }, [detail?.pages, query]);

  const selectProject = (projectId: string) => {
    setSelectedProjectId(projectId);
    setSelectedPath(null);
    setQuery("");
    setBrowserMode("knowledge");
    setViewMode("preview");
  };

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-background" data-testid="wiki-view">
      <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border/65 bg-background/90 px-4 sm:px-6">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <BookOpen className="h-4 w-4 shrink-0 text-primary" aria-hidden />
          <h1 className="truncate text-[15px] font-semibold tracking-wide text-foreground">
            {t("sidebar.wiki", { defaultValue: "Wiki" })}
          </h1>
          {selectedProject ? (
            <>
              <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground/60" aria-hidden />
              <label className="flex min-w-0 items-center rounded-lg border border-border/60 bg-muted/20 px-2">
                <span className="sr-only">Select Wiki project</span>
                <select value={selectedProjectId ?? ""} onChange={(event) => selectProject(event.target.value)} aria-label="Select Wiki project" className="h-8 min-w-0 max-w-[280px] bg-transparent text-[13px] font-medium text-foreground outline-none">
                  {projects.map((project) => <option key={project.id} value={project.id}>{project.title}</option>)}
                </select>
              </label>
            </>
          ) : null}
        </div>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-8 gap-1.5 rounded-full px-3 text-xs"
          onClick={onBackToChat}
        >
          {t("settings.backToChat", { defaultValue: "Back to chat" })}
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="h-8 w-8 rounded-full"
          aria-label={t("wiki.refresh", { defaultValue: "Refresh Wiki" })}
          onClick={() => setRefreshVersion((value) => value + 1)}
        >
          <RefreshCw className={cn("h-3.5 w-3.5", (loadingProjects || loadingDetail) && "animate-spin motion-reduce:animate-none")} />
        </Button>
      </header>

      {!sessionKey ? (
        <EmptyWikiState
          title={t("wiki.noSessionTitle", { defaultValue: "Open a workspace chat first" })}
          description={t("wiki.noSessionDescription", { defaultValue: "Wiki uses the active workspace scope. Open a chat, then return here to browse its knowledge projects." })}
          action={onBackToChat}
          actionLabel={t("settings.backToChat", { defaultValue: "Back to chat" })}
        />
      ) : (
        <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
          <PageList
            detail={detail}
            selectedPath={selectedPath}
            query={query}
            browserMode={browserMode}
            workspaceEntries={workspaceEntries}
            workspaceLoading={workspaceLoading}
            workspaceError={workspaceError}
            filteredPages={filteredPages}
            loading={loadingDetail}
            error={detailError || error}
            onQueryChange={setQuery}
            onBrowserModeChange={(mode) => {
              setBrowserMode(mode);
              setQuery("");
            }}
            onSelectPath={(path) => {
              setSelectedPath(path);
              setViewMode("preview");
            }}
          />
          <main className="flex min-h-0 min-w-0 flex-1 flex-col bg-background">
            <div className="flex h-11 shrink-0 items-center gap-2 border-b border-border/55 px-4">
              <span className="text-xs font-semibold text-foreground">{viewMode === "graph" ? "Knowledge graph" : "Document preview"}</span>
              {selectedPath ? <span className="min-w-0 flex-1 truncate text-[11px] text-muted-foreground" title={selectedPath}>{selectedPath}</span> : <span className="flex-1" />}
              <div className="flex items-center rounded-lg border border-border/60 p-0.5">
                <button type="button" aria-pressed={viewMode === "graph"} onClick={() => setViewMode("graph")} disabled={!detail?.graph} className={cn("flex items-center gap-1 rounded-md px-2 py-1 text-[10px]", viewMode === "graph" ? "bg-accent text-foreground" : "text-muted-foreground hover:text-foreground", !detail?.graph && "cursor-not-allowed opacity-45")}><Network className="h-3.5 w-3.5" aria-hidden />Graph</button>
                <button type="button" aria-pressed={viewMode === "preview"} onClick={() => setViewMode("preview")} className={cn("flex items-center gap-1 rounded-md px-2 py-1 text-[10px]", viewMode === "preview" ? "bg-accent text-foreground" : "text-muted-foreground hover:text-foreground")}><FileCode2 className="h-3.5 w-3.5" aria-hidden />Preview</button>
              </div>
              {viewMode === "graph" && selectedPath ? <Button type="button" variant="ghost" size="icon" className="h-7 w-7" aria-label="Close file preview" onClick={() => setSelectedPath(null)}><X className="h-3.5 w-3.5" aria-hidden /></Button> : null}
            </div>
            {viewMode === "graph" && detail ? (
              <div className="min-h-0 flex-1 overflow-auto xl:overflow-hidden">
                <div className="flex min-h-full flex-col xl:h-full xl:flex-row">
                  <div className="min-h-[340px] min-w-0 flex-1 border-b border-border/55 xl:min-h-0 xl:border-b-0 xl:border-r"><WikiGraph detail={detail} onSelectPath={setSelectedPath} /></div>
                  {selectedPath ? <section className="min-h-[420px] min-w-0 flex-1 xl:min-h-0 xl:w-[46%]" aria-label="Graph selected file"><FilePreviewPanel sessionKey={sessionKey} path={selectedPath} token={getToken()} embedded onClose={() => setSelectedPath(null)} onOpenFilePreview={setSelectedPath} onOpenReference={openWikiReference} onSaveContent={saveContent} /></section> : <GraphSelectionHint />}
                </div>
              </div>
            ) : selectedPath ? (
              <FilePreviewPanel sessionKey={sessionKey} path={selectedPath} token={getToken()} embedded onClose={() => setSelectedPath(null)} onOpenFilePreview={setSelectedPath} onOpenReference={openWikiReference} onSaveContent={saveContent} />
            ) : (
              <EmptyWikiState
                title={selectedProject ? t("wiki.selectPage", { defaultValue: "Select a Wiki page" }) : t("wiki.emptyTitle", { defaultValue: "No knowledge project selected" })}
                description={selectedProject ? "Choose a knowledge entry or file from the navigator, or open Graph to explore relationships." : t("wiki.emptyDescription", { defaultValue: "Create or publish a knowledge project from a chat to see it here." })}
              />
            )}
          </main>
        </div>
      )}
    </div>
  );
}

function PageList({
  detail,
  selectedPath,
  query,
  browserMode,
  workspaceEntries,
  workspaceLoading,
  workspaceError,
  filteredPages,
  loading,
  error,
  onQueryChange,
  onBrowserModeChange,
  onSelectPath,
}: {
  detail: KnowledgeProjectDetailPayload | null;
  selectedPath: string | null;
  query: string;
  browserMode: PageBrowserMode;
  workspaceEntries: WorkspaceTreeNode[];
  workspaceLoading: boolean;
  workspaceError: string | null;
  filteredPages: KnowledgeProjectDetailPayload["pages"];
  loading: boolean;
  error: string | null;
  onQueryChange: (value: string) => void;
  onBrowserModeChange: (mode: PageBrowserMode) => void;
  onSelectPath: (path: string) => void;
}) {
  const { t } = useTranslation();
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({
    overview: true,
    entity: true,
    concept: true,
    source: true,
    comparison: true,
    query: true,
    raw: true,
  });
  const groupedPages = useMemo(() => {
    const groups = new Map<string, typeof filteredPages>();
    for (const group of KNOWLEDGE_GROUPS) groups.set(group.key, []);
    for (const page of filteredPages) {
      const key = KNOWLEDGE_GROUPS.some((group) => group.key === page.type) ? page.type : "overview";
      groups.get(key)?.push(page);
    }
    return groups;
  }, [filteredPages]);
  const filteredWorkspaceEntries = useMemo(
    () => filterWorkspaceTree(workspaceEntries, query),
    [query, workspaceEntries],
  );

  return (
    <aside className="flex min-h-0 w-full shrink-0 flex-col border-b border-border/65 md:w-[330px] md:border-b-0 md:border-r lg:w-[360px] xl:w-[390px]" aria-label={t("wiki.pages", { defaultValue: "Wiki navigator" })}>
      <div className="shrink-0 border-b border-border/55 px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-foreground">{t("wiki.pages", { defaultValue: "Pages" })}</span>
          <span className="text-[11px] tabular-nums text-muted-foreground">{browserMode === "knowledge" ? detail?.pages.length ?? 0 : countWorkspaceFiles(workspaceEntries)}</span>
          <div className="ml-auto flex rounded-lg border border-border/60 p-0.5" role="tablist" aria-label="Wiki navigator mode">
            <button type="button" role="tab" aria-selected={browserMode === "knowledge"} onClick={() => onBrowserModeChange("knowledge")} className={cn("rounded-md px-2.5 py-1 text-[10px]", browserMode === "knowledge" ? "bg-accent text-foreground" : "text-muted-foreground hover:text-foreground")}>知识</button>
            <button type="button" role="tab" aria-selected={browserMode === "files"} onClick={() => onBrowserModeChange("files")} className={cn("rounded-md px-2.5 py-1 text-[10px]", browserMode === "files" ? "bg-accent text-foreground" : "text-muted-foreground hover:text-foreground")}>文件</button>
          </div>
        </div>
        <label className="mt-2 flex min-w-0 items-center gap-1.5 rounded-lg border border-border/60 bg-background px-2 text-muted-foreground">
          <Search className="h-3.5 w-3.5 shrink-0" aria-hidden />
          <span className="sr-only">{browserMode === "knowledge" ? "Search knowledge" : "Search files"}</span>
          <Input value={query} onChange={(event) => onQueryChange(event.target.value)} placeholder={browserMode === "knowledge" ? "Search knowledge" : "Search files"} className="h-8 min-w-0 border-0 bg-transparent px-0 text-xs shadow-none focus-visible:ring-0" />
        </label>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-2.5">
        {browserMode === "knowledge" ? (
          <>
            {loading ? <div className="flex items-center gap-2 px-2 py-4 text-xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" />Loading pages...</div> : null}
            {error ? <p className="px-2 py-4 text-xs text-destructive">{error}</p> : null}
            {!loading && !error && filteredPages.length === 0 ? <p className="px-2 py-4 text-xs text-muted-foreground">{t("wiki.noMatchingPages", { defaultValue: "No matching Wiki pages." })}</p> : null}
            {!loading && !error ? KNOWLEDGE_GROUPS.map((group) => {
              const pages = groupedPages.get(group.key) ?? [];
              const isExpanded = expandedGroups[group.key] ?? true;
              const Icon = group.icon;
              return (
                <div key={group.key} className="mb-1">
                  <button type="button" className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left text-xs font-medium hover:bg-accent/50" onClick={() => setExpandedGroups((current) => ({ ...current, [group.key]: !isExpanded }))} aria-expanded={isExpanded}>
                    {isExpanded ? <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" aria-hidden /> : <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />}
                    <Icon className={cn("h-4 w-4", group.color)} aria-hidden />
                    <span className="flex-1">{group.label}</span>
                    <span className="text-[10px] tabular-nums text-muted-foreground">{group.key === "raw" ? detail?.raw_files?.length ?? 0 : pages.length}</span>
                  </button>
                  {isExpanded ? (
                    <div className="ml-5 space-y-0.5 border-l border-border/50 pl-1.5">
                      {pages.map((page) => <KnowledgePageRow key={page.path} page={page} selectedPath={selectedPath} onSelectPath={onSelectPath} />)}
                      {group.key === "raw" ? detail?.raw_files?.map((path) => <button key={path} type="button" onClick={() => onSelectPath(path)} className={cn("flex w-full items-center rounded-md px-2 py-1.5 text-left text-[11px] text-muted-foreground hover:bg-accent/55 hover:text-foreground", selectedPath === path && "bg-accent/75 text-foreground")} title={path}><FileText className="mr-1.5 h-3.5 w-3.5 shrink-0" aria-hidden /><span className="truncate">{path}</span></button>) : null}
                    </div>
                  ) : null}
                </div>
              );
            }) : null}
            {detail ? <WikiStats detail={detail} onSelectPath={onSelectPath} /> : null}
          </>
        ) : (
          <>
            {workspaceLoading ? <div className="flex items-center gap-2 px-2 py-4 text-xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" />Loading files...</div> : null}
            {workspaceError ? <p className="px-2 py-4 text-xs text-destructive">{workspaceError}</p> : null}
            {!workspaceLoading && !workspaceError && filteredWorkspaceEntries.length === 0 ? <p className="px-2 py-4 text-xs text-muted-foreground">No matching files.</p> : null}
            {!workspaceLoading && !workspaceError ? filteredWorkspaceEntries.map((entry) => <TreeEntry key={entry.path} node={entry} selectedPath={selectedPath} onSelectPath={onSelectPath} />) : null}
          </>
        )}
      </div>
    </aside>
  );
}

function KnowledgePageRow({ page, selectedPath, onSelectPath }: { page: KnowledgeProjectDetailPayload["pages"][number]; selectedPath: string | null; onSelectPath: (path: string) => void }) {
  return (
    <button type="button" onClick={() => onSelectPath(page.path)} className={cn("flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-accent/55", selectedPath === page.path && "bg-accent/75 text-foreground")} title={page.path}>
      <FileCode2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary/70" aria-hidden />
      <span className="min-w-0 flex-1"><span className="block truncate text-[11px] font-medium">{page.title || page.slug}</span><span className="mt-0.5 block truncate text-[10px] text-muted-foreground">{page.type}{page.tags?.length ? ` · ${page.tags.slice(0, 2).join(" · ")}` : ""}</span></span>
    </button>
  );
}

function filterWorkspaceTree(entries: WorkspaceTreeNode[], query: string): WorkspaceTreeNode[] {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return entries;
  return entries.flatMap((entry) => {
    const children = entry.children ? filterWorkspaceTree(entry.children, query) : [];
    if (entry.path.toLocaleLowerCase().includes(needle) || entry.name.toLocaleLowerCase().includes(needle) || children.length > 0) {
      return [{ ...entry, children }];
    }
    return [];
  });
}

function countWorkspaceFiles(entries: WorkspaceTreeNode[]): number {
  return entries.reduce((count, entry) => count + (entry.kind === "file" ? 1 : countWorkspaceFiles(entry.children ?? [])), 0);
}

function WikiStats({ detail, onSelectPath }: { detail: KnowledgeProjectDetailPayload; onSelectPath: (path: string) => void }) {
  return (
    <div className="mt-4 border-t border-border/55 pt-3">
      <div className="grid grid-cols-3 gap-1.5 text-[10px] text-muted-foreground">
        <span>Sources {detail.counts.sources}</span><span>Entities {detail.counts.entities}</span><span>Relations {detail.counts.relations}</span>
      </div>
      <div className="mt-2 space-y-1">
        {detail.raw_files?.slice(0, 4).map((path) => <button key={path} type="button" onClick={() => onSelectPath(path)} className="block w-full truncate rounded px-1.5 py-1 text-left text-[10px] text-muted-foreground hover:bg-accent/55 hover:text-foreground" title={path}>{path}</button>)}
      </div>
    </div>
  );
}

type GraphLayoutSettings = {
  gravity: number;
  repulsion: number;
  linkForce: number;
  linkDistance: number;
};

const DEFAULT_GRAPH_LAYOUT: GraphLayoutSettings = {
  gravity: 0.8,
  repulsion: 4500,
  linkForce: 0.45,
  linkDistance: 90,
};

const GRAPH_COMMUNITY_META: Record<string, { label: string; color: string }> = {
  entity: { label: "实体", color: "#0ea5e9" },
  concept: { label: "概念", color: "#8b5cf6" },
  source: { label: "资料", color: "#f59e0b" },
  comparison: { label: "综合", color: "#f43f5e" },
  synthesis: { label: "综合", color: "#f43f5e" },
  overview: { label: "概览", color: "#f59e0b" },
  query: { label: "查询", color: "#10b981" },
};

function runGraphLayout(instance: cytoscape.Core, settings: GraphLayoutSettings) {
  const edgeElasticity = Math.round(8 + (1 - settings.linkForce) * 88);
  instance.layout({
    name: "cose",
    animate: true,
    animationDuration: 420,
    fit: true,
    padding: 24,
    gravity: settings.gravity,
    nodeRepulsion: settings.repulsion,
    edgeElasticity,
    idealEdgeLength: settings.linkDistance,
    componentSpacing: Math.max(40, settings.linkDistance),
    numIter: 500,
  }).run();
}

function GraphControl({
  label,
  value,
  min,
  max,
  step,
  displayValue,
  onChange,
  description,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  displayValue: string;
  onChange: (value: number) => void;
  description: string;
}) {
  return (
    <label className="min-w-0 space-y-1.5">
      <span className="flex items-center justify-between gap-2 text-[11px] font-medium text-foreground">
        <span>{label}</span>
        <output className="font-mono text-[10px] text-muted-foreground">{displayValue}</output>
      </span>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(event) => onChange(Number(event.target.value))} aria-label={label} title={description} className="h-1.5 w-full cursor-pointer accent-primary" />
      <span className="block truncate text-[10px] text-muted-foreground" title={description}>{description}</span>
    </label>
  );
}

export function resolveGraphPagePath(
  reference: string,
  title: string,
  pages: KnowledgeProjectDetailPayload["pages"],
): string | null {
  const references = new Map<string, string>();
  for (const page of pages) {
    for (const value of [page.slug, page.title, page.path]) {
      if (value) references.set(value.toLocaleLowerCase(), page.path);
    }
  }
  return references.get(reference.toLocaleLowerCase()) ?? references.get(title.toLocaleLowerCase()) ?? null;
}

type WikiGraphEdge = { source: string; target: string; relation?: string };

type GraphScope = { mode: "all" } | { mode: "neighborhood"; nodeId: string };
type MiniMapPoint = { id: string; x: number; y: number; color: string };
type MiniMapEdge = { source: string; target: string };

export function normalizeWikiGraphEdges(edges: WikiGraphEdge[], nodeIds: Set<string>, limit = 320): WikiGraphEdge[] {
  const undirected = new Map<string, WikiGraphEdge>();
  for (const edge of edges) {
    if (!nodeIds.has(edge.source) || !nodeIds.has(edge.target) || edge.source === edge.target) continue;
    const [source, target] = [edge.source, edge.target].sort((left, right) => left.localeCompare(right));
    const key = `${source}\u0000${target}`;
    const current = undirected.get(key);
    if (!current) {
      undirected.set(key, { source, target, relation: edge.relation || "related" });
      continue;
    }
    const relations = new Set([current.relation || "related", edge.relation || "related"]);
    current.relation = Array.from(relations).slice(0, 3).join(" · ");
  }
  return Array.from(undirected.values()).slice(0, limit);
}

function applyGraphScope(instance: cytoscape.Core, scope: GraphScope) {
  instance.elements().forEach((element) => {
    element.style("display", "element");
  });
  if (scope.mode !== "neighborhood") return;
  const node = instance.getElementById(scope.nodeId);
  if (node.empty()) return;
  const neighborhood = node.closedNeighborhood();
  instance.elements().forEach((element) => {
    if (!neighborhood.contains(element)) element.style("display", "none");
  });
}

function focusGraphNode(instance: cytoscape.Core, nodeId: string) {
  const node = instance.getElementById(nodeId);
  if (node.empty()) return;
  instance.nodes().removeClass("graph-search-match");
  node.addClass("graph-search-match");
  instance.animate({ fit: { eles: node.closedNeighborhood(), padding: 96 } }, { duration: 420 });
}

function WikiGraph({ detail, onSelectPath }: { detail: KnowledgeProjectDetailPayload; onSelectPath: (path: string) => void }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const graphRef = useRef<cytoscape.Core | null>(null);
  const miniMapSyncRef = useRef<(() => void) | null>(null);
  const [failed, setFailed] = useState(false);
  const [showControls, setShowControls] = useState(true);
  const [settings, setSettings] = useState<GraphLayoutSettings>(DEFAULT_GRAPH_LAYOUT);
  const [nodeQuery, setNodeQuery] = useState("");
  const [activeCommunity, setActiveCommunity] = useState<string | null>(null);
  const [hoveredRelation, setHoveredRelation] = useState<string | null>(null);
  const [graphScope, setGraphScope] = useState<GraphScope>({ mode: "all" });
  const [contextMenu, setContextMenu] = useState<{ nodeId: string; title: string; x: number; y: number } | null>(null);
  const [showMiniMap, setShowMiniMap] = useState(true);
  const [miniMap, setMiniMap] = useState<{ nodes: MiniMapPoint[]; edges: MiniMapEdge[] }>({ nodes: [], edges: [] });
  const graph = detail.graph;
  const normalizedRelationCount = useMemo(() => {
    if (!graph) return 0;
    return normalizeWikiGraphEdges(graph.edges, new Set(graph.nodes.map((node) => node.id))).length;
  }, [graph]);
  const communities = useMemo(() => {
    const counts = new Map<string, number>();
    for (const node of graph?.nodes ?? []) {
      const type = node.type || "entity";
      counts.set(type, (counts.get(type) ?? 0) + 1);
    }
    return Array.from(counts.entries()).map(([key, count]) => ({
      key,
      count,
      ...(GRAPH_COMMUNITY_META[key] ?? { label: key, color: "hsl(var(--primary))" }),
    }));
  }, [graph?.nodes]);
  useEffect(() => {
    const container = containerRef.current;
    if (!container || !graph) return undefined;
    const nodes = graph.nodes.filter((node) => node.id.trim()).slice(0, 160);
    const nodeIds = new Set(nodes.map((node) => node.id));
    const edges = normalizeWikiGraphEdges(graph.edges, nodeIds);
    const degreeByNode = new Map(nodes.map((node) => [node.id, 0]));
    edges.forEach((edge) => {
      degreeByNode.set(edge.source, (degreeByNode.get(edge.source) ?? 0) + 1);
      degreeByNode.set(edge.target, (degreeByNode.get(edge.target) ?? 0) + 1);
    });
    const maxDegree = Math.max(1, ...degreeByNode.values());
    let instance: cytoscape.Core;
    setFailed(false);
    try {
      instance = cytoscape({
        container,
        elements: [
          ...nodes.map((node) => ({ data: { id: node.id, label: (node.title || node.id).slice(0, 26), title: node.title || node.id, type: node.type || "entity", degree: degreeByNode.get(node.id) ?? 0 } })),
          ...edges.map((edge, index) => ({ data: { id: `${edge.source}:${edge.target}:${index}`, source: edge.source, target: edge.target, label: edge.relation || "related" } })),
        ],
        layout: { name: "preset" },
        style: [
          { selector: "node", style: { "background-color": "hsl(var(--primary))", "background-opacity": 0.82, color: "hsl(var(--foreground))", label: "data(label)", "font-size": 9, "text-wrap": "ellipsis", "text-max-width": "96px", "text-valign": "center", "text-halign": "center", width: `mapData(degree, 0, ${maxDegree}, 24, 64)`, height: `mapData(degree, 0, ${maxDegree}, 24, 64)` } },
          { selector: "edge", style: { width: 1, "line-color": "hsl(var(--muted-foreground))", "line-opacity": 0.32, "curve-style": "bezier" } },
          { selector: ".graph-dimmed", style: { opacity: 0.16 } },
          { selector: "node.graph-highlighted", style: { "border-width": 3, "border-color": "hsl(var(--primary))", "border-opacity": 0.95, "background-opacity": 1 } },
          { selector: "edge.graph-highlighted", style: { width: 3, "line-color": "hsl(var(--primary))", "line-opacity": 0.9 } },
          { selector: "node.graph-search-match", style: { "border-width": 4, "border-color": "#f59e0b", "border-opacity": 1, "background-opacity": 1 } },
          { selector: ".graph-community-muted", style: { opacity: 0.12 } },
          { selector: 'node[type = "entity"]', style: { "background-color": "#0ea5e9" } },
          { selector: 'node[type = "concept"]', style: { "background-color": "#8b5cf6" } },
          { selector: 'node[type = "source"]', style: { "background-color": "#f59e0b" } },
          { selector: 'node[type = "comparison"], node[type = "synthesis"]', style: { "background-color": "#f43f5e" } },
          { selector: 'node[type = "query"]', style: { "background-color": "#10b981" } },
        ],
        userZoomingEnabled: true,
        userPanningEnabled: true,
        boxSelectionEnabled: false,
      });
      graphRef.current = instance;
      const syncMiniMap = () => {
        const visibleNodes: cytoscape.NodeSingular[] = [];
        instance.nodes().forEach((node) => {
          if (node.visible()) visibleNodes.push(node);
        });
        if (visibleNodes.length === 0) {
          setMiniMap({ nodes: [], edges: [] });
          return;
        }
        const positions = visibleNodes.map((node) => node.position());
        const minX = Math.min(...positions.map((position) => position.x));
        const maxX = Math.max(...positions.map((position) => position.x));
        const minY = Math.min(...positions.map((position) => position.y));
        const maxY = Math.max(...positions.map((position) => position.y));
        const width = Math.max(1, maxX - minX);
        const height = Math.max(1, maxY - minY);
        const visibleIds = new Set(visibleNodes.map((node) => node.id()));
        const visibleEdges: cytoscape.EdgeSingular[] = [];
        instance.edges().forEach((edge) => {
          if (visibleIds.has(edge.source().id()) && visibleIds.has(edge.target().id())) visibleEdges.push(edge);
        });
        setMiniMap({
          nodes: visibleNodes.map((node) => {
            const position = node.position();
            const type = String(node.data("type") ?? "entity");
            return {
              id: node.id(),
              x: 6 + ((position.x - minX) / width) * 88,
              y: 6 + ((position.y - minY) / height) * 88,
              color: GRAPH_COMMUNITY_META[type]?.color ?? "hsl(var(--primary))",
            };
          }),
          edges: visibleEdges.map((edge) => ({ source: edge.source().id(), target: edge.target().id() })),
        });
      };
      miniMapSyncRef.current = syncMiniMap;
      instance.on("layoutstop dragfree", syncMiniMap);
      instance.on("tap", "node", (event) => {
        const target = event.target;
        const reference = String(target.id() ?? "");
        const title = String(target.data("title") ?? "");
        const path = resolveGraphPagePath(reference, title, detail.pages);
        setContextMenu(null);
        if (path) onSelectPath(path);
      });
      instance.on("tap", (event) => {
        if (event.target === instance) setContextMenu(null);
      });
      instance.on("cxttap", "node", (event) => {
        const target = event.target;
        const position = event.renderedPosition ?? { x: 16, y: 16 };
        const bounds = container.getBoundingClientRect();
        const menuWidth = 220;
        const menuHeight = 180;
        setContextMenu({
          nodeId: target.id(),
          title: String(target.data("title") ?? target.id()),
          x: Math.min(Math.max(position.x, 8), Math.max(8, bounds.width - menuWidth - 8)),
          y: Math.min(Math.max(position.y, 8), Math.max(8, bounds.height - menuHeight - 8)),
        });
      });
      instance.on("mouseover", "node", (event) => {
        const target = event.target;
        instance.elements().addClass("graph-dimmed");
        target.closedNeighborhood().removeClass("graph-dimmed").addClass("graph-highlighted");
        target.connectedEdges().removeClass("graph-dimmed").addClass("graph-highlighted");
      });
      instance.on("mouseout", "node", () => {
        instance.elements().removeClass("graph-dimmed graph-highlighted");
      });
      instance.on("mouseover", "edge", (event) => {
        const target = event.target;
        instance.elements().addClass("graph-dimmed");
        target.removeClass("graph-dimmed").addClass("graph-highlighted");
        target.connectedNodes().removeClass("graph-dimmed").addClass("graph-highlighted");
        setHoveredRelation(String(target.data("label") ?? "related"));
      });
      instance.on("mouseout", "edge", () => {
        instance.elements().removeClass("graph-dimmed graph-highlighted");
        setHoveredRelation(null);
      });
      runGraphLayout(instance, DEFAULT_GRAPH_LAYOUT);
      syncMiniMap();
    } catch {
      setFailed(true);
      return undefined;
    }
    return () => {
      miniMapSyncRef.current = null;
      graphRef.current = null;
      instance.destroy();
    };
  }, [detail.pages, graph, onSelectPath]);

  useEffect(() => {
    if (graphRef.current) runGraphLayout(graphRef.current, settings);
  }, [settings]);

  useEffect(() => {
    const instance = graphRef.current;
    if (!instance) return;
      instance.nodes().forEach((node) => {
        const muted = activeCommunity !== null && String(node.data("type") ?? "entity") !== activeCommunity;
        node.toggleClass("graph-community-muted", muted);
      });
      instance.edges().forEach((edge) => {
      let allNodesMuted = true;
      edge.connectedNodes().forEach((node) => {
        if (!node.hasClass("graph-community-muted")) allNodesMuted = false;
      });
      const muted = activeCommunity !== null && allNodesMuted;
      edge.toggleClass("graph-community-muted", muted);
    });
  }, [activeCommunity]);

  useEffect(() => {
    if (graphRef.current) {
      applyGraphScope(graphRef.current, graphScope);
      miniMapSyncRef.current?.();
    }
  }, [graphScope]);

  const updateSetting = (key: keyof GraphLayoutSettings, value: number) => {
    setSettings((current) => ({ ...current, [key]: value }));
  };

  const focusNode = () => {
    const instance = graphRef.current;
    const needle = nodeQuery.trim().toLocaleLowerCase();
    if (!instance || !needle) return;
    const match = instance.nodes().filter((node) => {
      const label = String(node.data("label") ?? "").toLocaleLowerCase();
      const title = String(node.data("title") ?? "").toLocaleLowerCase();
      return label.includes(needle) || title.includes(needle) || String(node.id()).toLocaleLowerCase().includes(needle);
    }).first();
    if (match.empty()) return;
    focusGraphNode(instance, match.id());
  };

  return (
    <section className="flex h-full min-h-0 flex-col" aria-label="Knowledge graph">
      <div className="flex min-h-12 shrink-0 items-center gap-2 border-b border-border/55 px-4">
        <SlidersHorizontal className="h-4 w-4 text-primary/80" aria-hidden />
        <span className="text-sm font-semibold">Layout controls</span>
        <span className="text-[11px] text-muted-foreground">Node size follows connection degree</span>
        <div className="ml-auto flex items-center gap-1">
          <label className="hidden min-w-0 items-center gap-1.5 rounded-md border border-border/60 bg-background px-2 text-muted-foreground sm:flex">
            <Search className="h-3.5 w-3.5 shrink-0" aria-hidden />
            <span className="sr-only">Search graph nodes</span>
            <Input value={nodeQuery} onChange={(event) => setNodeQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") focusNode(); }} placeholder="Search nodes" className="h-7 w-[130px] border-0 bg-transparent px-0 text-[11px] shadow-none focus-visible:ring-0" aria-label="Search graph nodes" />
          </label>
          <Button type="button" variant="ghost" size="sm" className="h-8 gap-1.5 px-2 text-xs sm:hidden" onClick={focusNode} aria-label="Focus graph search"><Search className="h-3.5 w-3.5" aria-hidden /></Button>
          <Button type="button" variant="ghost" size="sm" className="hidden h-8 gap-1.5 px-2 text-xs md:flex" onClick={() => setShowMiniMap((current) => !current)} aria-pressed={showMiniMap} aria-label="Toggle graph minimap"><PanelRightOpen className="h-3.5 w-3.5" aria-hidden />Mini map</Button>
          <Button type="button" variant="ghost" size="sm" className="h-8 gap-1.5 px-2 text-xs" onClick={() => setShowControls((current) => !current)} aria-expanded={showControls} aria-controls="wiki-graph-controls">{showControls ? "Hide controls" : "Tune layout"}</Button>
          <Button type="button" variant="ghost" size="icon" className="h-8 w-8" onClick={() => setSettings(DEFAULT_GRAPH_LAYOUT)} aria-label="Reset graph layout" title="Reset graph layout"><RotateCcw className="h-3.5 w-3.5" aria-hidden /></Button>
        </div>
      </div>
      {showControls ? <div id="wiki-graph-controls" className="grid shrink-0 grid-cols-1 gap-3 border-b border-border/55 bg-muted/15 px-4 py-3 sm:grid-cols-2 xl:grid-cols-4">
        <GraphControl label="向心力 / Gravity" value={settings.gravity} min={0} max={2} step={0.05} displayValue={settings.gravity.toFixed(2)} onChange={(value) => updateSetting("gravity", value)} description="拉拢各个连通分量到画布中心" />
        <GraphControl label="斥力 / Repulsion" value={settings.repulsion} min={500} max={12000} step={250} displayValue={String(settings.repulsion)} onChange={(value) => updateSetting("repulsion", value)} description="节点之间的排斥强度" />
        <GraphControl label="链接力 / Link force" value={settings.linkForce} min={0.1} max={1} step={0.05} displayValue={settings.linkForce.toFixed(2)} onChange={(value) => updateSetting("linkForce", value)} description="边把相连节点拉近的强度" />
        <GraphControl label="连接距离 / Link distance" value={settings.linkDistance} min={30} max={240} step={10} displayValue={`${settings.linkDistance}px`} onChange={(value) => updateSetting("linkDistance", value)} description="相连节点的理想间距" />
      </div> : null}
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-border/55 px-4"><GitBranch className="h-4 w-4 text-primary/80" aria-hidden /><span className="text-sm font-semibold">Knowledge graph</span><span className="text-[11px] text-muted-foreground">{graph?.nodes.length ?? 0} nodes · {normalizedRelationCount} undirected relations</span></div>
      <div className="flex min-h-10 shrink-0 items-center gap-1.5 overflow-x-auto border-b border-border/55 px-4 py-1.5" aria-label="Graph communities">
        <span className="mr-1 shrink-0 text-[10px] text-muted-foreground">社区</span>
        <button type="button" aria-pressed={activeCommunity === null} onClick={() => setActiveCommunity(null)} className={cn("shrink-0 rounded-full border px-2 py-1 text-[10px] transition-colors", activeCommunity === null ? "border-primary/30 bg-primary/10 text-foreground" : "border-border/60 text-muted-foreground hover:text-foreground")}>全部</button>
        {communities.map((community) => (
          <button key={community.key} type="button" aria-pressed={activeCommunity === community.key} onClick={() => setActiveCommunity((current) => current === community.key ? null : community.key)} className={cn("flex shrink-0 items-center gap-1 rounded-full border px-2 py-1 text-[10px] transition-colors", activeCommunity === community.key ? "border-primary/30 bg-primary/10 text-foreground" : "border-border/60 text-muted-foreground hover:text-foreground")}>
            <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: community.color }} aria-hidden />
            {community.label} {community.count}
          </button>
        ))}
        {hoveredRelation ? <span className="ml-auto shrink-0 rounded-md bg-accent/60 px-2 py-1 text-[10px] text-foreground">关系：{hoveredRelation}</span> : null}
      </div>
      <div ref={containerRef} className="relative min-h-0 flex-1 bg-background" data-knowledge-graph-canvas>
        {!graph || graph.nodes.length === 0 ? <span className="flex h-full items-center justify-center text-sm text-muted-foreground">No graph data</span> : null}
        {failed ? <span className="flex h-full items-center justify-center px-4 text-center text-sm text-muted-foreground">Graph preview unavailable</span> : null}
        {showMiniMap && miniMap.nodes.length > 0 ? <GraphMiniMap miniMap={miniMap} /> : null}
        {contextMenu ? <GraphContextMenu contextMenu={contextMenu} pages={detail.pages} graphRef={graphRef} onOpenFile={onSelectPath} onClose={() => setContextMenu(null)} onScopeChange={setGraphScope} /> : null}
      </div>
    </section>
  );
}

function GraphMiniMap({ miniMap }: { miniMap: { nodes: MiniMapPoint[]; edges: MiniMapEdge[] } }) {
  const points = new Map(miniMap.nodes.map((node) => [node.id, node]));
  return (
    <div className="pointer-events-none absolute bottom-3 right-3 z-10 w-40 rounded-lg border border-border/70 bg-background/90 p-2 shadow-lg backdrop-blur" aria-label="Graph minimap">
      <div className="mb-1 text-[9px] font-medium uppercase tracking-[0.12em] text-muted-foreground">Overview</div>
      <svg viewBox="0 0 100 100" className="h-28 w-full" role="img" aria-label="Graph overview">
        <rect x="0" y="0" width="100" height="100" rx="5" fill="hsl(var(--muted) / 0.22)" />
        {miniMap.edges.map((edge, index) => {
          const source = points.get(edge.source);
          const target = points.get(edge.target);
          if (!source || !target) return null;
          return <line key={`${edge.source}:${edge.target}:${index}`} x1={source.x} y1={source.y} x2={target.x} y2={target.y} stroke="hsl(var(--muted-foreground))" strokeOpacity="0.28" strokeWidth="0.6" />;
        })}
        {miniMap.nodes.map((node) => <circle key={node.id} cx={node.x} cy={node.y} r="2.2" fill={node.color} fillOpacity="0.9" />)}
      </svg>
    </div>
  );
}

function GraphContextMenu({ contextMenu, pages, graphRef, onOpenFile, onClose, onScopeChange }: { contextMenu: { nodeId: string; title: string; x: number; y: number }; pages: KnowledgeProjectDetailPayload["pages"]; graphRef: { current: cytoscape.Core | null }; onOpenFile: (path: string) => void; onClose: () => void; onScopeChange: (scope: GraphScope) => void }) {
  const path = resolveGraphPagePath(contextMenu.nodeId, contextMenu.title, pages);
  const focus = () => {
    if (graphRef.current) focusGraphNode(graphRef.current, contextMenu.nodeId);
    onClose();
  };
  return (
    <div role="menu" aria-label={`Actions for ${contextMenu.title}`} className="absolute z-20 w-52 rounded-lg border border-border/70 bg-popover p-1.5 text-popover-foreground shadow-xl" style={{ left: contextMenu.x, top: contextMenu.y }} onPointerDown={(event) => event.stopPropagation()}>
      <div className="truncate px-2 py-1.5 text-[11px] font-medium" title={contextMenu.title}>{contextMenu.title}</div>
      {path ? <button type="button" role="menuitem" className="w-full rounded-md px-2 py-1.5 text-left text-xs hover:bg-accent" onClick={() => { onOpenFile(path); onClose(); }}>打开 Wiki 文件</button> : <span className="block px-2 py-1.5 text-[10px] text-muted-foreground">此节点没有对应文件</span>}
      <button type="button" role="menuitem" className="w-full rounded-md px-2 py-1.5 text-left text-xs hover:bg-accent" onClick={focus}>聚焦节点</button>
      <button type="button" role="menuitem" className="w-full rounded-md px-2 py-1.5 text-left text-xs hover:bg-accent" onClick={() => { onScopeChange({ mode: "neighborhood", nodeId: contextMenu.nodeId }); onClose(); }}>仅显示一阶关系</button>
      <button type="button" role="menuitem" className="w-full rounded-md px-2 py-1.5 text-left text-xs hover:bg-accent" onClick={() => { onScopeChange({ mode: "all" }); onClose(); }}>显示全部节点</button>
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
            {open ? <ChevronDown className="h-3.5 w-3.5" aria-hidden /> : <ChevronRight className="h-3.5 w-3.5" aria-hidden />}
          </button>
        ) : <span className="w-6" />}
        <button type="button" className="flex min-w-0 flex-1 items-center gap-1.5 py-1 pr-1 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => directory ? setOpen((value) => !value) : onSelectPath(node.path)}>
          {directory ? (open ? <FolderOpen className="h-3.5 w-3.5 shrink-0 text-amber-500/80" aria-hidden /> : <Folder className="h-3.5 w-3.5 shrink-0 text-amber-500/80" aria-hidden />) : <FileCode2 className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />}
          <span className="min-w-0 truncate">{node.name}</span>
        </button>
      </div>
      {directory && open && node.children?.map((child) => <TreeEntry key={child.path} node={child} selectedPath={selectedPath} onSelectPath={onSelectPath} depth={depth + 1} />)}
    </div>
  );
}

function GraphSelectionHint() {
  return (
    <section className="flex min-h-[260px] min-w-0 flex-1 items-center justify-center border-t border-border/55 xl:border-t-0" aria-label="Graph file selection">
      <div className="max-w-xs px-6 text-center text-muted-foreground">
        <PanelRightOpen className="mx-auto h-8 w-8 opacity-35" aria-hidden />
        <p className="mt-3 text-sm font-medium text-foreground">Select a node to open its file</p>
        <p className="mt-1 text-xs leading-5">Click a node in the graph to keep the graph visible and open the linked Wiki file here.</p>
      </div>
    </section>
  );
}

function EmptyWikiState({ title, description, action, actionLabel }: { title: string; description: string; action?: () => void; actionLabel?: string }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center px-6 text-center">
      <BookOpen className="h-10 w-10 text-muted-foreground/35" aria-hidden />
      <h2 className="mt-4 text-base font-semibold text-foreground">{title}</h2>
      <p className="mt-2 max-w-md text-sm leading-6 text-muted-foreground">{description}</p>
      {action && actionLabel ? <Button type="button" variant="outline" className="mt-5 rounded-full" onClick={action}>{actionLabel}</Button> : null}
    </div>
  );
}
