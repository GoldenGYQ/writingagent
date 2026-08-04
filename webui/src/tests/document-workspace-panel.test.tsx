import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DocumentWorkspacePanel } from "@/components/thread/DocumentWorkspacePanel";
import { fetchKnowledgeProject, fetchWritingRuntime, fetchWorkspaceTree } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchKnowledgeProject: vi.fn(),
    fetchWritingRuntime: vi.fn(),
    fetchWorkspaceTree: vi.fn(),
  };
});

describe("DocumentWorkspacePanel", () => {
  beforeEach(() => {
    vi.mocked(fetchWorkspaceTree).mockResolvedValue({
      root: "/workspace",
      path: ".",
      depth: 4,
      limit: 400,
      truncated: false,
      entries: [],
    });
    vi.mocked(fetchWritingRuntime).mockResolvedValue({ active: false });
    vi.mocked(fetchKnowledgeProject).mockResolvedValue({
      project: {
        id: "kb_runtime",
        title: "Runtime Knowledge",
        phase: "compiled",
        task: null,
      },
      counts: { sources: 1, ir_files: 1, entities: 2, relations: 1, pages: 2, reviews: 0 },
      paths: { raw: "raw", ir: "knowledge/ir", wiki: "wiki", graph: "knowledge/graph/graph.json" },
      raw_files: ["raw/sources/runtime.md"],
      ir_files: ["knowledge/ir/runtime.json"],
      pages: [{ slug: "runtime", path: "wiki/concepts/runtime.md", type: "concept" }],
      graph: {
        nodes: [
          { id: "runtime", type: "concept", title: "Agent Runtime" },
          { id: "tool", type: "entity", title: "Tool" },
        ],
        edges: [{ source: "runtime", target: "tool", relation: "uses" }],
      },
    });
  });

  it("renders the knowledge graph through the workspace preview", async () => {
    const user = userEvent.setup();
    render(
      <DocumentWorkspacePanel
        sessionKey="websocket:chat-1"
        token="token"
        selectedPath={null}
        desktopWidth={560}
        knowledgeProjectId="kb_runtime"
        onSelectPath={() => {}}
        onClose={() => {}}
      />,
    );

    await waitFor(() => expect(screen.getByText("Runtime Knowledge")).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: /Show graph preview/i }));
    expect(screen.getByLabelText("Knowledge graph preview")).toBeInTheDocument();
    expect(screen.getByTestId("document-workspace-panel")).toBeInTheDocument();
  });
});
