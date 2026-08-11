import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { normalizeWikiGraphEdges, resolveGraphPagePath, WikiView } from "@/components/wiki/WikiView";
import { fetchKnowledgeProject, fetchKnowledgeProjects, fetchWorkspaceTree } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchKnowledgeProject: vi.fn(),
    fetchKnowledgeProjects: vi.fn(),
    fetchWorkspaceTree: vi.fn(),
  };
});

const getToken = () => "token";

vi.mock("@/providers/ClientProvider", () => ({
  useClient: () => ({ getToken }),
}));

vi.mock("@/components/FilePreviewPanel", () => ({
  FilePreviewPanel: ({ path }: { path: string }) => <div data-testid="wiki-file-preview">Preview: {path}</div>,
}));

describe("WikiView", () => {
  it("maps graph node ids and titles back to Wiki files", () => {
    const pages = [
      { slug: "runtime", path: "wiki/concepts/runtime.md", type: "concept", title: "Agent Runtime" },
    ];
    expect(resolveGraphPagePath("RUNTIME", "", pages)).toBe("wiki/concepts/runtime.md");
    expect(resolveGraphPagePath("unknown", "Agent Runtime", pages)).toBe("wiki/concepts/runtime.md");
    expect(resolveGraphPagePath("unknown", "missing", pages)).toBeNull();
  });

  it("normalizes reciprocal graph relations into one undirected edge", () => {
    expect(normalizeWikiGraphEdges([
      { source: "runtime", target: "tool", relation: "uses" },
      { source: "tool", target: "runtime", relation: "related" },
      { source: "runtime", target: "runtime", relation: "self" },
    ], new Set(["runtime", "tool"]))).toEqual([
      { source: "runtime", target: "tool", relation: "uses · related" },
    ]);
  });

  beforeEach(() => {
    vi.mocked(fetchKnowledgeProjects).mockResolvedValue({
      projects: [{ id: "kb-runtime", title: "Runtime Knowledge", phase: "published", page_count: 2, source_count: 1 }],
    });
    vi.mocked(fetchKnowledgeProject).mockResolvedValue({
      project: { id: "kb-runtime", title: "Runtime Knowledge", phase: "published" },
      counts: { sources: 1, ir_files: 1, entities: 2, relations: 1, pages: 2, reviews: 0 },
      paths: { raw: "raw", ir: "ir", wiki: "wiki", graph: "graph.json" },
      pages: [
        { slug: "runtime", path: "wiki/concepts/runtime.md", type: "concept", title: "Agent Runtime", tags: ["agent"] },
        { slug: "tool", path: "wiki/entities/tool.md", type: "entity", title: "Tool" },
      ],
      raw_files: ["raw/runtime.md"],
      graph: { nodes: [{ id: "runtime", type: "concept", title: "Agent Runtime" }], edges: [] },
    });
    vi.mocked(fetchWorkspaceTree).mockResolvedValue({
      root: "workspace",
      path: "",
      depth: 6,
      limit: 600,
      truncated: false,
      entries: [{ name: "articles", path: "articles", kind: "directory", children: [{ name: "draft.md", path: "articles/draft.md", kind: "file" }] }],
    });
  });

  it("renders as an independent Wiki surface with project and page navigation", async () => {
    const user = userEvent.setup();
    render(<WikiView sessionKey="websocket:chat-1" onBackToChat={() => {}} />);

    expect(screen.getByTestId("wiki-view")).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByText("Runtime Knowledge").length).toBeGreaterThan(0));
    expect(await screen.findByText("Agent Runtime")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Tool/ }));
    expect(screen.getByTestId("wiki-file-preview")).toHaveTextContent("wiki/entities/tool.md");
    await user.click(screen.getByRole("button", { name: "Graph", exact: true }));
    expect(screen.getByRole("button", { name: "Graph", exact: true })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("region", { name: "Knowledge graph" })).toBeInTheDocument();
    const graphHighlights = screen.getByLabelText("Graph communities");
    expect(graphHighlights).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "社区" })).toHaveAttribute("aria-selected", "true");
    await user.click(screen.getByRole("tab", { name: "节点类型" }));
    expect(within(graphHighlights).getByRole("button", { name: /概念/ })).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "标签" }));
    expect(within(graphHighlights).getByRole("button", { name: /agent/ })).toBeInTheDocument();
    const minimapToggle = screen.getByRole("button", { name: "Toggle graph minimap" });
    expect(minimapToggle).toHaveAttribute("aria-pressed", "true");
    await user.click(minimapToggle);
    expect(minimapToggle).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByTestId("wiki-file-preview")).toHaveTextContent("wiki/entities/tool.md");
    const gravity = screen.getByRole("slider", { name: /Gravity/ });
    expect(gravity).toBeInTheDocument();
    expect(screen.getByRole("slider", { name: /Repulsion/ })).toBeInTheDocument();
    expect(screen.getByRole("slider", { name: /Link force/ })).toBeInTheDocument();
    expect(screen.getByRole("slider", { name: /Link distance/ })).toBeInTheDocument();
    fireEvent.change(gravity, { target: { value: "1.25" } });
    expect(gravity).toHaveValue("1.25");
    await user.click(screen.getByRole("button", { name: "Reset graph layout" }));
    expect(gravity).toHaveValue("0.8");
    expect(screen.getByRole("tab", { name: "知识" })).toHaveAttribute("aria-selected", "true");
    await user.click(screen.getByRole("tab", { name: "文件" }));
    expect(await screen.findByText("draft.md")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "draft.md" }));
    expect(screen.getByTestId("wiki-file-preview")).toHaveTextContent("articles/draft.md");
    expect(fetchWorkspaceTree).toHaveBeenCalledWith("token", "websocket:chat-1", { depth: 6, limit: 600 });
    expect(fetchKnowledgeProjects).toHaveBeenCalledWith("token", "websocket:chat-1");
    expect(fetchKnowledgeProject).toHaveBeenCalledWith("token", "websocket:chat-1", "kb-runtime");
  });
});
