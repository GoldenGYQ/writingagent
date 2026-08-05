import { useEffect, useRef } from "react";
import { EditorState } from "@codemirror/state";
import { defaultHighlightStyle, indentOnInput, syntaxHighlighting } from "@codemirror/language";
import { javascript } from "@codemirror/lang-javascript";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import { python } from "@codemirror/lang-python";
import { EditorView, keymap, lineNumbers } from "@codemirror/view";
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";

import { cn } from "@/lib/utils";

interface SourceEditorProps {
  value: string;
  language?: string;
  onChange: (value: string) => void;
  className?: string;
  ariaLabel?: string;
}

function languageExtension(language?: string) {
  const normalized = language?.trim().toLowerCase();
  if (normalized === "markdown" || normalized === "md" || normalized === "mdx") return markdown();
  if (normalized === "json" || normalized === "jsonl") return json();
  if (normalized === "python" || normalized === "py") return python();
  if (normalized === "typescript" || normalized === "ts" || normalized === "tsx") {
    return javascript({ typescript: true, jsx: normalized === "tsx" });
  }
  if (normalized === "javascript" || normalized === "js" || normalized === "jsx") {
    return javascript({ jsx: normalized === "jsx" });
  }
  return [];
}

const editorTheme = EditorView.theme({
  "&": {
    height: "100%",
    backgroundColor: "transparent",
    color: "hsl(var(--foreground))",
    fontSize: "13px",
  },
  ".cm-scroller": {
    overflow: "auto",
    fontFamily: '"JetBrains Mono", "SFMono-Regular", Consolas, monospace',
  },
  ".cm-content": {
    minHeight: "100%",
    padding: "16px 18px 32px 8px",
    caretColor: "hsl(var(--foreground))",
  },
  ".cm-line": {
    padding: "0 8px",
    lineHeight: "1.6",
  },
  ".cm-gutters": {
    border: "0",
    backgroundColor: "transparent",
    color: "hsl(var(--muted-foreground))",
    paddingLeft: "10px",
  },
  ".cm-activeLine, .cm-activeLineGutter": {
    backgroundColor: "hsl(var(--accent) / 0.45)",
  },
  ".cm-selectionBackground, ::selection": {
    backgroundColor: "hsl(var(--primary) / 0.22) !important",
  },
});

export function SourceEditor({
  value,
  language,
  onChange,
  className,
  ariaLabel = "Source editor",
}: SourceEditorProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const viewRef = useRef<EditorView | null>(null);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  useEffect(() => {
    if (!hostRef.current) return undefined;
    const state = EditorState.create({
      doc: value,
      extensions: [
        lineNumbers(),
        history(),
        indentOnInput(),
        keymap.of([...defaultKeymap, ...historyKeymap]),
        syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
        languageExtension(language),
        editorTheme,
        EditorView.updateListener.of((update) => {
          if (update.docChanged) onChangeRef.current(update.state.doc.toString());
        }),
        EditorView.contentAttributes.of({ "aria-label": ariaLabel, spellcheck: "false" }),
      ],
    });
    const view = new EditorView({ state, parent: hostRef.current });
    viewRef.current = view;
    return () => {
      viewRef.current = null;
      view.destroy();
    };
    // The parent remounts this editor when a new file/language is selected.
    // Keeping this effect stable avoids resetting the cursor on every keystroke.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const view = viewRef.current;
    if (!view || view.state.doc.toString() === value) return;
    view.dispatch({
      changes: { from: 0, to: view.state.doc.length, insert: value },
    });
  }, [value]);

  return <div ref={hostRef} className={cn("h-full min-h-0 overflow-hidden", className)} data-testid="source-editor" />;
}
