import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FilePreviewPanel, selectionRangeInContent } from "@/components/FilePreviewPanel";
import { setAppLanguage } from "@/i18n";
import { fetchFilePreview } from "@/lib/api";

vi.mock("@/components/CodeBlock", () => ({
  CodeBlock: ({
    code,
    language,
    highlight,
    showLineNumbers,
  }: {
    code: string;
    language?: string;
    highlight?: boolean;
    showLineNumbers?: boolean;
  }) => (
    <pre
      data-testid="mock-code-block"
      data-language={language}
      data-highlight={String(highlight)}
    >
      {showLineNumbers
        ? code.split("\n").map((line, index) => (
          <span key={index} data-file-line={index + 1}>
            {line}
            {index < code.split("\n").length - 1 ? "\n" : null}
          </span>
        ))
        : code}
    </pre>
  ),
}));

vi.mock("@/components/MarkdownText", () => ({
  MarkdownText: ({ children }: { children: string }) => <div data-testid="mock-markdown-text">{children}</div>,
}));

vi.mock("@/components/SourceEditor", () => ({
  SourceEditor: ({
    value,
    onChange,
  }: {
    value: string;
    onChange: (value: string) => void;
  }) => (
    <textarea
      data-testid="source-editor"
      value={value}
      onChange={(event) => onChange(event.target.value)}
    />
  ),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchFilePreview: vi.fn(),
  };
});

describe("FilePreviewPanel", () => {
  beforeEach(async () => {
    await setAppLanguage("en");
    vi.mocked(fetchFilePreview).mockReset();
  });

  it("anchors multi-line selections even when the renderer adds line prefixes or whitespace", () => {
    expect(
      selectionRangeInContent(
        "alpha\nbeta\ngamma\ndelta",
        "1 alpha\n  beta  \n3 gamma",
      ),
    ).toMatchObject({ startLine: 1, endLine: 3, quote: "1 alpha\n  beta  \n3 gamma" });
  });

  it("emits a multi-line file citation from the rendered line anchors", async () => {
    const user = userEvent.setup();
    const onFileCitation = vi.fn();
    const selection = {
      toString: () => "alpha\nbeta\ngamma",
      rangeCount: 1,
      isCollapsed: false,
      getRangeAt: () => ({
        intersectsNode: (node: Node) => {
          const line = Number((node as HTMLElement).dataset.fileLine);
          return line >= 1 && line <= 3;
        },
      }),
    } as unknown as Selection;
    const getSelection = vi.spyOn(window, "getSelection").mockReturnValue(selection);
    vi.mocked(fetchFilePreview).mockResolvedValue({
      path: "notes.md",
      display_path: "notes.md",
      language: "markdown",
      content: "alpha\nbeta\ngamma\ndelta",
      truncated: false,
    });

    render(
      <FilePreviewPanel
        sessionKey="websocket:chat-1"
        path="notes.md"
        token="tok"
        onClose={() => {}}
        onFileCitation={onFileCitation}
      />,
    );

    const selectable = await screen.findByTestId("file-preview-selectable");
    fireEvent.mouseUp(selectable);
    await waitFor(() => expect(screen.getByRole("button", { name: /cite selection/i })).toBeVisible());
    expect(screen.getByRole("button", { name: /cite selection/i })).toHaveTextContent("L1-3");

    await user.click(screen.getByRole("button", { name: /cite selection/i }));
    expect(onFileCitation).toHaveBeenCalledWith({
      path: "notes.md",
      start_line: 1,
      end_line: 3,
      quote: "alpha\nbeta\ngamma",
    });
    expect(screen.queryByRole("button", { name: /cite selection/i })).not.toBeInTheDocument();
    getSelection.mockRestore();
  });

  it("shows a compact breadcrumb with one file name and a visible close action", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    vi.mocked(fetchFilePreview).mockResolvedValue({
      path: "/Users/hr/workspace/quicksort.py",
      display_path: "quicksort.py",
      language: "python",
      content: "print('ok')",
      truncated: false,
    });

    render(
      <FilePreviewPanel
        sessionKey="websocket:chat-1"
        path="quicksort.py"
        token="tok"
        onClose={onClose}
      />,
    );

    const codeBlock = await screen.findByTestId("mock-code-block");
    expect(codeBlock).toHaveTextContent("print('ok')");
    expect(codeBlock).toHaveAttribute("data-language", "python");
    expect(codeBlock).toHaveAttribute("data-highlight", "true");
    expect(screen.getByTestId("file-preview-breadcrumb")).toHaveTextContent("...");
    expect(screen.getByTestId("file-preview-breadcrumb")).toHaveTextContent("workspace");
    expect(screen.getByTestId("file-preview-title")).toHaveTextContent("quicksort.py");
    expect(screen.getAllByText("quicksort.py")).toHaveLength(1);

    const closeButton = screen.getByRole("button", { name: "Close file preview" });
    expect(closeButton).toBeVisible();

    await user.click(closeButton);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("updates translated chrome without refetching the open file", async () => {
    vi.mocked(fetchFilePreview).mockResolvedValue({
      path: "/workspace/notes.md",
      display_path: "notes.md",
      language: "markdown",
      content: "# Notes",
      truncated: false,
    });

    render(
      <FilePreviewPanel
        sessionKey="websocket:chat-1"
        path="notes.md"
        token="tok"
        onClose={() => {}}
      />,
    );

    await screen.findByTestId("mock-markdown-text");
    expect(fetchFilePreview).toHaveBeenCalledTimes(1);

    await act(async () => {
      await setAppLanguage("zh-CN");
    });

    expect(fetchFilePreview).toHaveBeenCalledTimes(1);
  });

  it("saves source edits through the Writing ChangeSet callback", async () => {
    const user = userEvent.setup();
    const onSaveContent = vi.fn().mockResolvedValue({
      request_id: "req-1",
      chat_id: "chat-1",
      ok: true,
      status: "review",
      changeset: { id: "changeset-1" },
      revision: null,
    });
    vi.mocked(fetchFilePreview).mockResolvedValue({
      path: "/workspace/writing/project-1/documents/doc-1/chapters/chapter-1.md",
      display_path: "writing/project-1/documents/doc-1/chapters/chapter-1.md",
      language: "markdown",
      content: "# Draft",
      truncated: false,
    });

    render(
      <FilePreviewPanel
        sessionKey="websocket:chat-1"
        path="writing/project-1/documents/doc-1/chapters/chapter-1.md"
        token="tok"
        onClose={() => {}}
        onSaveContent={onSaveContent}
      />,
    );

    await screen.findByTestId("mock-markdown-text");
    await user.click(screen.getByRole("tab", { name: /source/i }));
    const editor = screen.getByTestId("source-editor");
    await user.clear(editor);
    await user.type(editor, "# Revised");
    await user.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(onSaveContent).toHaveBeenCalledWith({
      path: "/workspace/writing/project-1/documents/doc-1/chapters/chapter-1.md",
      content: "# Revised",
      reason: "Edit from WebUI source editor",
    }));
    expect(await screen.findByText("ChangeSet pending approval")).toBeVisible();
    expect(screen.getByTestId("mock-markdown-text")).toHaveTextContent("# Revised");
  });
});
