import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, PointerEvent as ReactPointerEvent } from "react";
import { AlertCircle, ChevronRight, Loader2, Quote, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { CodeBlock } from "@/components/CodeBlock";
import { splitFilePath } from "@/components/FileReferenceChip";
import { ApiError, fetchFilePreview } from "@/lib/api";
import type { FileCitation, FilePreviewPayload } from "@/lib/types";
import { cn } from "@/lib/utils";

interface FilePreviewPanelProps {
  sessionKey: string;
  path: string;
  token: string;
  desktopWidth?: number;
  isClosing?: boolean;
  onResizeStart?: (event: ReactPointerEvent<HTMLButtonElement>) => void;
  onClose: () => void;
  embedded?: boolean;
  refreshKey?: number;
  onFileCitation?: (citation: FileCitation) => void;
}

type PreviewState =
  | { status: "loading" }
  | { status: "error"; error: unknown }
  | { status: "ready"; payload: FilePreviewPayload };

interface SelectionRange {
  startLine: number;
  endLine: number;
  quote: string;
}

function normalizeSelectionText(value: string): string {
  return value
    .replace(/\r\n/g, "\n")
    .replace(/\u00a0/g, " ")
    .replace(/\u2028/g, "\n")
    .trimEnd();
}

function normalizedSelectionLine(value: string): string {
  // Some syntax-highlighter renderers expose line-number text through the
  // browser selection even though it is visually non-selectable.
  return value.replace(/^\s*\d+\s+/, "").trim();
}

export function selectionRangeInContent(content: string, rawSelection: string): SelectionRange | null {
  const quote = normalizeSelectionText(rawSelection);
  if (!quote.trim()) return null;
  const normalizedContent = content.replace(/\r\n/g, "\n");
  const exactOffset = normalizedContent.indexOf(quote);
  if (exactOffset >= 0) {
    const startLine = normalizedContent.slice(0, exactOffset).split("\n").length;
    return {
      startLine,
      endLine: startLine + quote.split("\n").length - 1,
      quote,
    };
  }

  // Multi-line selections from Prism can contain renderer whitespace or
  // line-number prefixes. Recover the line anchor without altering the quote
  // that is sent to the model.
  const contentLines = normalizedContent.split("\n");
  const expectedLines = quote.split("\n").map(normalizedSelectionLine);
  const nonEmptyExpectedLines = expectedLines.filter(Boolean);
  const first = nonEmptyExpectedLines[0];
  const last = nonEmptyExpectedLines[nonEmptyExpectedLines.length - 1] ?? first;
  if (!first) return null;

  // A syntax highlighter may insert whitespace or token boundaries between
  // lines. The first and last selected lines are stable anchors; the DOM
  // line range (when available) supplies the exact span, while this fallback
  // keeps multi-line selection working in plain-text and older renderers.
  for (let start = 0; start < contentLines.length; start += 1) {
    if (!normalizedSelectionLine(contentLines[start]).includes(first)) continue;
    for (let end = start; end < contentLines.length; end += 1) {
      const actualLast = normalizedSelectionLine(contentLines[end]);
      if (!actualLast.includes(last)) continue;
      if (expectedLines.length > 1 && end === start) continue;
      return { startLine: start + 1, endLine: end + 1, quote };
    }
  }
  return null;
}

export function FilePreviewPanel({
  sessionKey,
  path,
  token,
  desktopWidth = 544,
  isClosing = false,
  onResizeStart,
  onClose,
  embedded = false,
  refreshKey = 0,
  onFileCitation,
}: FilePreviewPanelProps) {
  const { t } = useTranslation();
  const [state, setState] = useState<PreviewState>({ status: "loading" });
  const [entered, setEntered] = useState(false);
  const [selection, setSelection] = useState<FileCitation | null>(null);
  const selectableRef = useRef<HTMLDivElement | null>(null);
  const tokenRef = useRef(token);
  tokenRef.current = token;

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => setEntered(true));
    return () => window.cancelAnimationFrame(frame);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    fetchFilePreview(tokenRef.current, sessionKey, path)
      .then((payload) => {
        if (!cancelled) setState({ status: "ready", payload });
      })
      .catch((error: unknown) => {
        if (!cancelled) setState({ status: "error", error });
      });
    return () => {
      cancelled = true;
    };
  }, [path, sessionKey, refreshKey]);

  useEffect(() => setSelection(null), [path, refreshKey]);

  const captureSelection = useCallback(() => {
    if (state.status !== "ready" || !onFileCitation) return;
    const nativeSelection = window.getSelection();
    const selected = nativeSelection?.toString() ?? "";
    if (!nativeSelection || nativeSelection.rangeCount === 0 || nativeSelection.isCollapsed) {
      setSelection(null);
      return;
    }
    if (!selected.trim()) {
      setSelection(null);
      return;
    }

    const lineNodes = selectableRef.current
      ? Array.from(selectableRef.current.querySelectorAll<HTMLElement>("[data-file-line]"))
      : [];
    const selectedLineNumbers = lineNodes
      .filter((node) => {
        try {
          return nativeSelection.getRangeAt(0).intersectsNode(node);
        } catch {
          return false;
        }
      })
      .map((node) => Number(node.dataset.fileLine))
      .filter((line): line is number => Number.isFinite(line));
    const domStartLine = selectedLineNumbers.length > 0 ? Math.min(...selectedLineNumbers) : null;
    const domEndLine = selectedLineNumbers.length > 0 ? Math.max(...selectedLineNumbers) : null;
    const range = selectionRangeInContent(state.payload.content, selected);
    if (!range) {
      if (domStartLine === null || domEndLine === null) {
        setSelection(null);
        return;
      }
      setSelection({
        path: state.payload.path,
        start_line: domStartLine,
        end_line: domEndLine,
        quote: normalizeSelectionText(selected),
      });
      return;
    }
    setSelection({
      path: state.payload.path,
      start_line: domStartLine ?? range.startLine,
      end_line: domEndLine ?? range.endLine,
      quote: range.quote,
    });
  }, [onFileCitation, state]);

  const scheduleSelectionCapture = useCallback(() => {
    window.requestAnimationFrame(captureSelection);
  }, [captureSelection]);

  const displayPath = state.status === "ready" ? state.payload.display_path : path;
  const previewPath = state.status === "ready" ? state.payload.path : displayPath;
  const normalizedPreviewPath = previewPath.replace(/\\/g, "/");
  const hasRootPrefix = normalizedPreviewPath.startsWith("/");
  const { name } = splitFilePath(displayPath);
  const fileName = name || displayPath;
  const pathParts = useMemo(
    () => normalizedPreviewPath.split("/").filter(Boolean),
    [normalizedPreviewPath],
  );
  const directoryParts = useMemo(
    () => (pathParts.length > 1 ? pathParts.slice(0, -1) : []),
    [pathParts],
  );
  const breadcrumbParts = useMemo(
    () => (directoryParts.length > 0 ? [...directoryParts, fileName] : [fileName]),
    [directoryParts, fileName],
  );
  const compactBreadcrumbParts = useMemo(
    () => (breadcrumbParts.length > 3 ? breadcrumbParts.slice(-3) : breadcrumbParts),
    [breadcrumbParts],
  );
  const hasCompactPrefix = breadcrumbParts.length > compactBreadcrumbParts.length;
  const breadcrumbTitle = `${hasRootPrefix ? "/" : ""}${[
    ...directoryParts,
    fileName,
  ].join("/")}`;
  const errorMessage = state.status === "error"
    ? (state.error instanceof ApiError
      ? (state.error.status === 404 && /API route not found/i.test(state.error.message)
        ? t("filePreview.routeMissing", {
          defaultValue: "File preview needs the latest gateway. Restart nanobot gateway and try again.",
        })
        : state.error.message)
      : t("filePreview.failed", { defaultValue: "Could not preview this file." }))
    : null;

  return (
    <aside
      aria-label={t("filePreview.aria", { defaultValue: "File preview" })}
      style={{
        "--file-preview-width": `${desktopWidth}px`,
        "--file-preview-slot-width": !entered || isClosing ? "0px" : `${desktopWidth}px`,
      } as CSSProperties}
      className={cn(
        embedded
          ? "relative flex h-full w-full min-w-0 overflow-hidden"
          : "absolute inset-y-0 right-0 z-30 w-[min(100vw,var(--file-preview-slot-width))] overflow-hidden transition-[width] duration-300 ease-out will-change-[width] md:relative md:z-auto md:w-[var(--file-preview-slot-width)] md:min-w-0 md:shrink-0",
        !embedded && isClosing && "pointer-events-none",
      )}
      data-testid="file-preview-panel"
      data-file-preview-panel
    >
      <div
        className={cn(
          embedded
            ? "flex h-full w-full min-w-0 flex-col overflow-hidden bg-background"
            : "absolute inset-y-0 right-0 flex w-[min(100vw,var(--file-preview-width))] flex-col overflow-hidden border-l border-border/70 bg-background pb-[env(safe-area-inset-bottom)] shadow-2xl transition-[opacity,transform] duration-300 ease-out will-change-transform md:w-[var(--file-preview-width)] md:pb-0 md:shadow-none",
          !embedded && (!entered || isClosing ? "translate-x-full opacity-0" : "translate-x-0 opacity-100"),
          !embedded && "motion-reduce:translate-x-0",
        )}
      >
        {!embedded && onResizeStart ? (
          <button
            type="button"
            aria-label={t("filePreview.resize", { defaultValue: "Resize file preview" })}
            className={cn(
              "group absolute inset-y-0 left-0 z-20 hidden w-3 -translate-x-1/2 cursor-col-resize touch-none md:flex",
              "items-stretch justify-center focus-visible:outline-none",
            )}
            onPointerDown={onResizeStart}
          >
            <span
              aria-hidden
              className={cn(
                "h-full w-px bg-foreground/25 opacity-0 transition-opacity",
                "group-hover:opacity-100 group-focus-visible:bg-ring group-focus-visible:opacity-100",
              )}
            />
          </button>
        ) : null}
        <div className="flex min-h-0 flex-1 flex-col">
          <div
            className="flex h-11 shrink-0 items-center gap-2 border-b border-border/60 px-3"
            title={previewPath}
          >
            <nav
              aria-label={t("filePreview.breadcrumb", { defaultValue: "File path" })}
              className="flex min-w-0 flex-1 items-center overflow-hidden text-sm leading-5"
              title={breadcrumbTitle}
              data-testid="file-preview-breadcrumb"
            >
              {hasCompactPrefix ? (
                <>
                  <span className="shrink-0 text-muted-foreground/55">...</span>
                  <ChevronRight
                    className="mx-1 h-3.5 w-3.5 shrink-0 text-muted-foreground/35"
                    aria-hidden
                  />
                </>
              ) : hasRootPrefix ? (
                <>
                  <span className="shrink-0 text-muted-foreground/55">/</span>
                  <ChevronRight
                    className="mx-1 h-3.5 w-3.5 shrink-0 text-muted-foreground/35"
                    aria-hidden
                  />
                </>
              ) : null}
              {compactBreadcrumbParts.map((part, index) => {
                const isLast = index === compactBreadcrumbParts.length - 1;
                return (
                  <span
                    key={`${part}-${index}`}
                    className="flex min-w-0 items-center overflow-hidden"
                  >
                    {index > 0 ? (
                      <ChevronRight
                        className="mx-1 h-3.5 w-3.5 shrink-0 text-muted-foreground/35"
                        aria-hidden
                      />
                    ) : null}
                    <span
                      className={cn(
                        "min-w-0 truncate rounded-[4px] px-1 py-0.5",
                        isLast
                          ? "font-medium text-foreground"
                          : "max-w-[26vw] shrink text-muted-foreground/78",
                      )}
                      data-testid={isLast ? "file-preview-title" : undefined}
                    >
                      {part}
                    </span>
                  </span>
                );
              })}
            </nav>
            <button
              type="button"
              onClick={onClose}
              className={cn(
                "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md",
                "text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              )}
              title={t("filePreview.close", { defaultValue: "Close file preview" })}
              aria-label={t("filePreview.close", { defaultValue: "Close file preview" })}
              data-testid="file-preview-close"
            >
              <X className="h-4 w-4" aria-hidden />
            </button>
          </div>

          <div className="relative min-h-0 flex-1 overflow-auto">
            {state.status === "loading" ? (
              <div className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                {t("filePreview.loading", { defaultValue: "Loading preview..." })}
              </div>
            ) : state.status === "error" ? (
              <div className="flex h-full items-center justify-center px-8 text-center text-sm text-muted-foreground">
                <div className="max-w-sm">
                  <AlertCircle
                    className="mx-auto mb-3 h-5 w-5 text-muted-foreground/70"
                    aria-hidden
                  />
                  <p>{errorMessage}</p>
                </div>
              </div>
            ) : (
              <div
                ref={selectableRef}
                className="min-h-full"
                data-testid="file-preview-selectable"
                data-file-preview-selectable
                onMouseUp={scheduleSelectionCapture}
                onPointerUp={scheduleSelectionCapture}
                onKeyUp={scheduleSelectionCapture}
              >
                {selection ? (
                  <div className="sticky top-2 z-10 flex justify-end px-3">
                    <button
                      type="button"
                      className="inline-flex items-center gap-1.5 rounded-md border border-border/70 bg-background/95 px-2.5 py-1.5 text-xs font-medium text-foreground shadow-sm hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      onMouseUp={(event) => event.stopPropagation()}
                      onPointerUp={(event) => event.stopPropagation()}
                      onClick={() => {
                        onFileCitation?.(selection);
                        setSelection(null);
                      }}
                    >
                      <Quote className="h-3.5 w-3.5" aria-hidden />
                      {t("filePreview.quoteSelection", { defaultValue: "引用选中内容" })}
                      <span className="text-muted-foreground">L{selection.start_line}-{selection.end_line}</span>
                    </button>
                  </div>
                ) : null}
                {state.payload.truncated ? (
                  <div className="mx-4 mt-3 rounded-md border border-amber-500/25 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-200">
                    {t("filePreview.truncated", {
                      defaultValue: "Preview is truncated because this file is large.",
                    })}
                  </div>
                ) : null}
                <CodeBlock
                  language={state.payload.language}
                  code={state.payload.content}
                  chrome="none"
                  highlight
                  showLineNumbers
                  wrapLongLines={false}
                  className="min-h-full"
                />
              </div>
            )}
          </div>
        </div>
      </div>
    </aside>
  );
}
