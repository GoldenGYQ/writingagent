import {
  ArrowUpRight,
  BookOpen,
  CalendarDays,
  CircleUserRound,
  ExternalLink,
  FileText,
  GitCompare,
  Layers3,
  Lightbulb,
  Search,
  Tag,
} from "lucide-react";

import { cn } from "@/lib/utils";

export type MarkdownMetadataValue = string | string[];

export interface MarkdownMetadata {
  [key: string]: MarkdownMetadataValue;
}

export interface ParsedMarkdownFrontmatter {
  metadata: MarkdownMetadata;
  body: string;
}

const FRONTMATTER_RE = /^(?:\uFEFF)?---[ \t]*\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)/;

const CORE_KEYS = new Set([
  "type",
  "title",
  "tags",
  "related",
  "sources",
  "created",
  "updated",
]);

const TYPE_META: Record<string, {
  label: string;
  icon: typeof CircleUserRound;
  iconClass: string;
  badgeClass: string;
}> = {
  entity: {
    label: "ENTITY",
    icon: CircleUserRound,
    iconClass: "text-blue-600 dark:text-blue-300",
    badgeClass: "border-blue-500/20 bg-blue-500/10 text-blue-700 dark:text-blue-300",
  },
  concept: {
    label: "CONCEPT",
    icon: Lightbulb,
    iconClass: "text-violet-600 dark:text-violet-300",
    badgeClass: "border-violet-500/20 bg-violet-500/10 text-violet-700 dark:text-violet-300",
  },
  source: {
    label: "SOURCE",
    icon: FileText,
    iconClass: "text-orange-600 dark:text-orange-300",
    badgeClass: "border-orange-500/20 bg-orange-500/10 text-orange-700 dark:text-orange-300",
  },
  comparison: {
    label: "COMPARISON",
    icon: GitCompare,
    iconClass: "text-rose-600 dark:text-rose-300",
    badgeClass: "border-rose-500/20 bg-rose-500/10 text-rose-700 dark:text-rose-300",
  },
  synthesis: {
    label: "SYNTHESIS",
    icon: Layers3,
    iconClass: "text-rose-600 dark:text-rose-300",
    badgeClass: "border-rose-500/20 bg-rose-500/10 text-rose-700 dark:text-rose-300",
  },
  query: {
    label: "QUERY",
    icon: Search,
    iconClass: "text-emerald-600 dark:text-emerald-300",
    badgeClass: "border-emerald-500/20 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  },
  overview: {
    label: "OVERVIEW",
    icon: BookOpen,
    iconClass: "text-amber-600 dark:text-amber-300",
    badgeClass: "border-amber-500/20 bg-amber-500/10 text-amber-700 dark:text-amber-300",
  },
};

function parseScalar(raw: string): MarkdownMetadataValue {
  const value = raw.trim();
  if (!value) return "";

  try {
    const parsed: unknown = JSON.parse(value);
    if (Array.isArray(parsed)) {
      return parsed
        .filter((item): item is string | number | boolean =>
          typeof item === "string" || typeof item === "number" || typeof item === "boolean")
        .map(String);
    }
    if (typeof parsed === "string" || typeof parsed === "number" || typeof parsed === "boolean") {
      return String(parsed);
    }
  } catch {
    // Generated pages use JSON-compatible values, but accept simple YAML arrays
    // from legacy/BoClaw-style pages as a compatibility fallback.
  }

  if (value.startsWith("[") && value.endsWith("]")) {
    return value
      .slice(1, -1)
      .split(",")
      .map((item) => item.trim().replace(/^(?:"([\s\S]*)"|'([\s\S]*)')$/, "$1$2"))
      .filter(Boolean);
  }

  return value.replace(/^(?:"([\s\S]*)"|'([\s\S]*)')$/, "$1$2");
}

/** Parse the bounded frontmatter contract emitted by the Knowledge compiler. */
export function parseMarkdownFrontmatter(source: string): ParsedMarkdownFrontmatter | null {
  const match = FRONTMATTER_RE.exec(source);
  if (!match) return null;

  const metadata: MarkdownMetadata = {};
  for (const line of match[1].split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const separator = trimmed.indexOf(":");
    if (separator <= 0) continue;
    const key = trimmed.slice(0, separator).trim();
    if (!/^[A-Za-z][A-Za-z0-9_-]*$/.test(key)) continue;
    metadata[key] = parseScalar(trimmed.slice(separator + 1));
  }

  if (Object.keys(metadata).length === 0) return null;
  return { metadata, body: source.slice(match[0].length) };
}

function listValue(metadata: MarkdownMetadata, key: string): string[] {
  const value = metadata[key];
  return Array.isArray(value) ? value.filter(Boolean) : value ? [value] : [];
}

function scalarValue(metadata: MarkdownMetadata, key: string): string {
  const value = metadata[key];
  return Array.isArray(value) ? value.join(", ") : value ?? "";
}

function isExternalUrl(value: string): boolean {
  return /^https?:\/\//i.test(value);
}

function MetadataReference({
  value,
  onOpen,
  kind,
}: {
  value: string;
  onOpen?: (value: string) => void;
  kind: "source" | "related";
}) {
  if (isExternalUrl(value)) {
    return (
      <a
        href={value}
        target="_blank"
        rel="noreferrer noopener"
        className="group inline-flex max-w-full items-center gap-1 rounded-full border border-border/70 bg-background/75 px-2.5 py-1 text-[12px] font-medium text-foreground transition-colors hover:border-primary/35 hover:bg-accent hover:text-primary"
        aria-label={`Open ${kind} ${value}`}
      >
        <span className="min-w-0 truncate">{value}</span>
        <ExternalLink className="h-3 w-3 shrink-0 text-muted-foreground group-hover:text-primary" aria-hidden />
      </a>
    );
  }

  if (!onOpen) {
    return (
      <span className="inline-flex max-w-full items-center gap-1 rounded-full border border-border/70 bg-background/75 px-2.5 py-1 text-[12px] font-medium text-foreground">
        <span className="min-w-0 truncate">{value}</span>
      </span>
    );
  }

  return (
    <button
      type="button"
      onClick={() => onOpen(value)}
      className="group inline-flex max-w-full items-center gap-1 rounded-full border border-border/70 bg-background/75 px-2.5 py-1 text-left text-[12px] font-medium text-foreground transition-colors hover:border-primary/35 hover:bg-accent hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      aria-label={`Open ${kind} ${value}`}
    >
      <span className="min-w-0 truncate">{value}</span>
      <ArrowUpRight className="h-3 w-3 shrink-0 text-muted-foreground group-hover:text-primary" aria-hidden />
    </button>
  );
}

export function MarkdownMetadataCard({
  metadata,
  onOpenFilePreview,
  onOpenReference,
}: {
  metadata: MarkdownMetadata;
  onOpenFilePreview?: (path: string) => void;
  onOpenReference?: (reference: string) => void;
}) {
  const rawType = scalarValue(metadata, "type").toLowerCase() || "concept";
  const typeMeta = TYPE_META[rawType] ?? {
    label: rawType.toUpperCase(),
    icon: BookOpen,
    iconClass: "text-slate-600 dark:text-slate-300",
    badgeClass: "border-slate-500/20 bg-slate-500/10 text-slate-700 dark:text-slate-300",
  };
  const TypeIcon = typeMeta.icon;
  const title = scalarValue(metadata, "title") || "Untitled page";
  const tags = listValue(metadata, "tags");
  const related = listValue(metadata, "related");
  const sources = listValue(metadata, "sources");
  const updated = scalarValue(metadata, "updated");
  const created = scalarValue(metadata, "created");
  const externalUrl = scalarValue(metadata, "url");
  const extraEntries = Object.entries(metadata).filter(([key]) => !CORE_KEYS.has(key) && key !== "url");

  return (
    <section
      className="mb-5 overflow-hidden rounded-2xl border border-border/70 bg-gradient-to-br from-background via-background to-muted/25 shadow-sm"
      data-testid="markdown-metadata-card"
      aria-label="Document metadata"
    >
      <div className="p-4 sm:p-5">
        <div className="flex items-start gap-3">
          <div className="grid h-12 w-12 shrink-0 place-items-center rounded-xl bg-primary/10 ring-1 ring-primary/10">
            <TypeIcon className={cn("h-6 w-6", typeMeta.iconClass)} aria-hidden />
          </div>
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-lg font-semibold tracking-tight text-foreground" title={title}>{title}</h2>
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              <span className={cn("rounded-md border px-2 py-0.5 text-[10px] font-semibold tracking-[0.12em]", typeMeta.badgeClass)}>{typeMeta.label}</span>
              {created ? <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground"><CalendarDays className="h-3.5 w-3.5" aria-hidden />{created}</span> : null}
              {tags.map((tag) => <span key={tag} className="inline-flex max-w-full items-center gap-1 rounded-md border border-border/70 bg-background/80 px-2 py-0.5 text-[11px] text-muted-foreground"><Tag className="h-3 w-3 shrink-0" aria-hidden /><span className="truncate">{tag}</span></span>)}
            </div>
          </div>
        </div>

        {sources.length > 0 ? (
          <div className="mt-5">
            <div className="mb-2 flex items-center gap-2 text-xs font-semibold text-muted-foreground"><Layers3 className="h-4 w-4" aria-hidden />Sources <span className="font-normal tabular-nums">({sources.length})</span></div>
            <div className="flex flex-wrap gap-2">
              {sources.map((source) => <MetadataReference key={source} value={source} kind="source" onOpen={onOpenFilePreview} />)}
            </div>
          </div>
        ) : null}

        {related.length > 0 ? (
          <div className="mt-5">
            <div className="mb-2 flex items-center gap-2 text-xs font-semibold text-muted-foreground"><ArrowUpRight className="h-4 w-4" aria-hidden />Related <span className="font-normal tabular-nums">({related.length})</span></div>
            <div className="flex flex-wrap gap-2">
              {related.map((reference) => <MetadataReference key={reference} value={reference} kind="related" onOpen={onOpenReference} />)}
            </div>
          </div>
        ) : null}
      </div>

      {updated || externalUrl || extraEntries.length > 0 ? (
        <div className="mx-4 mb-4 rounded-xl border border-border/65 bg-muted/15 px-3.5 py-3 sm:mx-5 sm:mb-5">
          <div className="mb-2 text-xs font-semibold text-muted-foreground">More</div>
          <div className="space-y-1.5 text-[11px]">
            {updated ? <div className="flex gap-3"><span className="w-16 shrink-0 text-muted-foreground">updated:</span><span className="font-medium text-foreground">{updated}</span></div> : null}
            {externalUrl ? <div className="flex min-w-0 gap-3"><span className="w-16 shrink-0 text-muted-foreground">url:</span><a href={externalUrl} target="_blank" rel="noreferrer noopener" className="min-w-0 truncate text-primary underline-offset-2 hover:underline" title={externalUrl}>{externalUrl}</a></div> : null}
            {extraEntries.map(([key, value]) => <div key={key} className="flex min-w-0 gap-3"><span className="w-16 shrink-0 text-muted-foreground">{key}:</span><span className="min-w-0 truncate text-foreground">{Array.isArray(value) ? value.join(", ") : value}</span></div>)}
          </div>
        </div>
      ) : null}
    </section>
  );
}
