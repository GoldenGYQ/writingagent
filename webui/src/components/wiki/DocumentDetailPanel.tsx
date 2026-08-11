import { useMemo } from "react";
import {
  ArrowLeft,
  ChevronRight,
  FileText,
  PanelRightClose,
  Tag,
  X,
} from "lucide-react";

import { FilePreviewPanel } from "@/components/FilePreviewPanel";
import type {
  KnowledgeProjectDetailPayload,
  WritingChangeSetResult,
} from "@/lib/types";

type KnowledgePage = KnowledgeProjectDetailPayload["pages"][number];
type KnowledgeNode = NonNullable<KnowledgeProjectDetailPayload["graph"]>["nodes"][number];

interface DocumentDetailPanelProps {
  sessionKey: string;
  token: string;
  detail: KnowledgeProjectDetailPayload;
  page: KnowledgePage | null;
  node: KnowledgeNode | null;
  path: string | null;
  onClose: () => void;
  onOpenPath: (path: string) => void;
  onOpenReference: (reference: string) => void;
  onSaveContent?: (request: { path: string; content: string; reason?: string }) => Promise<WritingChangeSetResult>;
}

function resolvePage(reference: string, pages: KnowledgePage[]): KnowledgePage | null {
  const needle = reference.toLocaleLowerCase();
  return pages.find((candidate) => (
    [candidate.slug, candidate.title, candidate.path]
      .filter(Boolean)
      .some((value) => String(value).toLocaleLowerCase() === needle)
  )) ?? null;
}

function pageForNode(node: KnowledgeNode | null, pages: KnowledgePage[]): KnowledgePage | null {
  if (!node) return null;
  return resolvePage(node.id, pages) ?? resolvePage(node.title ?? "", pages);
}

function relatedPagesForNode(
  node: KnowledgeNode | null,
  page: KnowledgePage | null,
  detail: KnowledgeProjectDetailPayload,
): KnowledgePage[] {
  const references = new Set<string>(page?.related ?? []);
  if (node && detail.graph) {
    for (const edge of detail.graph.edges) {
      if (edge.source === node.id) references.add(edge.target);
      if (edge.target === node.id) references.add(edge.source);
    }
  }
  const values = new Map<string, KnowledgePage>();
  for (const reference of references) {
    const candidate = resolvePage(reference, detail.pages);
    if (candidate && candidate.path !== page?.path) values.set(candidate.path, candidate);
  }
  return Array.from(values.values()).slice(0, 12);
}

function TagPill({ children, color }: { children: string; color?: string }) {
  return (
    <span
      className="inline-flex max-w-full items-center gap-1 rounded-full border border-border/70 bg-muted/35 px-2 py-1 text-[10px] text-muted-foreground"
      title={children}
    >
      {color ? <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ backgroundColor: color }} aria-hidden /> : <Tag className="h-3 w-3 shrink-0" aria-hidden />}
      <span className="truncate">{children}</span>
    </span>
  );
}

function DetailOverview({
  detail,
  onOpenPath,
}: {
  detail: KnowledgeProjectDetailPayload;
  onOpenPath: (path: string) => void;
}) {
  const recentPages = detail.pages.slice(0, 6);
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <div className="border-b border-border/65 bg-card px-4 py-5">
        <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-primary">Knowledge workspace</p>
        <h2 className="mt-2 text-lg font-semibold tracking-tight text-foreground">{detail.project.title}</h2>
        <p className="mt-2 text-xs leading-5 text-muted-foreground">选择图谱节点或左侧文档，查看文档内容、来源与关联关系。</p>
      </div>
      <div className="grid grid-cols-3 gap-2 border-b border-border/65 p-4 text-center">
        <DetailMetric label="节点" value={detail.graph?.nodes.length ?? detail.counts.pages} />
        <DetailMetric label="关系" value={detail.counts.relations} />
        <DetailMetric label="来源" value={detail.counts.sources} />
      </div>
      <div className="space-y-4 p-4">
        <section>
          <h3 className="text-xs font-semibold text-foreground">使用方式</h3>
          <ul className="mt-2 space-y-2 text-xs leading-5 text-muted-foreground">
            <li>点击节点查看对应文档和一跳关系。</li>
            <li>使用图内高亮面板按社区、类型或标签定位。</li>
            <li>Preview 与 Source 可随时切换，引用仍保留文件行号。</li>
          </ul>
        </section>
        <section>
          <div className="flex items-center justify-between gap-2">
            <h3 className="text-xs font-semibold text-foreground">最近文档</h3>
            <span className="text-[10px] text-muted-foreground">{recentPages.length}</span>
          </div>
          <div className="mt-2 space-y-1.5">
            {recentPages.map((candidate) => (
              <button
                key={candidate.path}
                type="button"
                className="flex min-h-10 w-full items-center gap-2 rounded-lg border border-transparent px-2.5 py-2 text-left text-xs text-muted-foreground transition-colors hover:border-border/70 hover:bg-muted/50 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                onClick={() => onOpenPath(candidate.path)}
                title={candidate.path}
              >
                <FileText className="h-3.5 w-3.5 shrink-0 text-primary/70" aria-hidden />
                <span className="min-w-0 flex-1 truncate">{candidate.title || candidate.slug}</span>
                <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground/60" aria-hidden />
              </button>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

function DetailMetric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border border-border/65 bg-background px-2 py-2">
      <div className="text-sm font-semibold tabular-nums text-foreground">{value}</div>
      <div className="mt-0.5 text-[10px] text-muted-foreground">{label}</div>
    </div>
  );
}

export function DocumentDetailPanel({
  sessionKey,
  token,
  detail,
  page: pageProp,
  node,
  path,
  onClose,
  onOpenPath,
  onOpenReference,
  onSaveContent,
}: DocumentDetailPanelProps) {
  const page = pageProp ?? pageForNode(node, detail.pages);
  const relatedPages = useMemo(() => relatedPagesForNode(node, page, detail), [detail, node, page]);

  return (
    <aside className="flex min-h-0 min-w-0 flex-1 flex-col border-l border-border/65 bg-background xl:flex-none xl:w-[var(--wiki-detail-width)]" aria-label="Document details">
      <div className="flex min-h-12 shrink-0 items-center gap-2 border-b border-border/65 px-4">
        <button
          type="button"
          className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          onClick={onClose}
          aria-label="Collapse document details"
          title="Collapse document details"
        >
          <PanelRightClose className="h-4 w-4" aria-hidden />
        </button>
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs font-semibold text-foreground">{page?.title || node?.title || "Workspace overview"}</p>
          <p className="truncate text-[10px] text-muted-foreground">{page?.path || path || "Knowledge workspace"}</p>
        </div>
        {page || node ? (
          <button type="button" onClick={onClose} className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-label="Close document details" title="Close document details">
            <X className="h-4 w-4" aria-hidden />
          </button>
        ) : null}
      </div>

      {!page && !node && !path ? <DetailOverview detail={detail} onOpenPath={onOpenPath} /> : (
        <>
          <div className="shrink-0 border-b border-border/65 bg-card px-4 py-4">
            <div className="flex items-start gap-2">
              <button type="button" onClick={onClose} className="mt-0.5 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-label="Back to workspace overview" title="Back to workspace overview">
                <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
              </button>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-1.5">
                  {page?.type ? <span className="rounded-full bg-primary/10 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-primary">{page.type}</span> : null}
                  {node?.community_label ? <span className="rounded-full bg-muted px-2 py-1 text-[10px] text-muted-foreground">{node.community_label}</span> : null}
                </div>
                <h2 className="mt-2 break-words text-base font-semibold leading-6 text-foreground">{page?.title || node?.title || path}</h2>
              </div>
            </div>
            {page?.tags?.length ? <div className="mt-3 flex flex-wrap gap-1.5">{page.tags.map((tag) => <TagPill key={tag}>{tag}</TagPill>)}</div> : null}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">
            <section className="border-b border-border/65 px-4 py-4">
              <h3 className="text-xs font-semibold text-foreground">Sources</h3>
              {page?.sources?.length ? (
                <div className="mt-2 space-y-1.5">
                  {page.sources.map((source) => (
                    <button key={source} type="button" className="flex min-h-9 w-full items-start gap-2 rounded-lg border border-dashed border-border/70 px-2.5 py-2 text-left text-[11px] text-muted-foreground hover:border-primary/40 hover:bg-primary/5 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => onOpenReference(source)} title={source}>
                      <FileText className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary/70" aria-hidden />
                      <span className="min-w-0 break-all">{source}</span>
                    </button>
                  ))}
                </div>
              ) : <p className="mt-2 text-[11px] text-muted-foreground">暂无来源信息</p>}
            </section>
            <section className="border-b border-border/65 px-4 py-4">
              <div className="flex items-center justify-between gap-2">
                <h3 className="text-xs font-semibold text-foreground">Related</h3>
                <span className="text-[10px] tabular-nums text-muted-foreground">{relatedPages.length}</span>
              </div>
              {relatedPages.length ? <div className="mt-2 flex flex-wrap gap-1.5">{relatedPages.map((related) => <button key={related.path} type="button" className="inline-flex max-w-full items-center gap-1 rounded-full border border-border/70 bg-background px-2.5 py-1.5 text-[10px] text-muted-foreground hover:border-primary/40 hover:bg-primary/5 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => onOpenReference(related.slug || related.title || related.path)} title={related.path}>{related.title || related.slug}<ChevronRight className="h-3 w-3 shrink-0" aria-hidden /></button>)}</div> : <p className="mt-2 text-[11px] text-muted-foreground">暂无关联文档</p>}
            </section>
            <section className="px-4 py-4">
              <h3 className="text-xs font-semibold text-foreground">More</h3>
              <dl className="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-2 text-[10px]">
                {node?.degree !== undefined ? <><dt className="text-muted-foreground">连接度</dt><dd className="text-right tabular-nums text-foreground">{node.degree}</dd></> : null}
                {node?.centrality !== undefined ? <><dt className="text-muted-foreground">中心性</dt><dd className="text-right tabular-nums text-foreground">{node.centrality.toFixed(2)}</dd></> : null}
                {node?.community_size !== undefined ? <><dt className="text-muted-foreground">社区规模</dt><dd className="text-right tabular-nums text-foreground">{node.community_size}</dd></> : null}
                <dt className="text-muted-foreground">知识库更新时间</dt><dd className="truncate text-right text-foreground" title={detail.project.updated_at}>{detail.project.updated_at || "—"}</dd>
              </dl>
            </section>
            {path ? <div className="min-h-[360px] border-t border-border/65"><FilePreviewPanel sessionKey={sessionKey} path={path} token={token} embedded onClose={onClose} onOpenFilePreview={onOpenPath} onOpenReference={onOpenReference} onSaveContent={onSaveContent} /></div> : null}
          </div>
        </>
      )}
    </aside>
  );
}
