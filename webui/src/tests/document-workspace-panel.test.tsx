import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DocumentWorkspacePanel } from "@/components/thread/DocumentWorkspacePanel";
import { fetchWritingRuntime, fetchWorkspaceTree } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
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
  });

  it("keeps the chat workspace focused on files", async () => {
    render(
      <DocumentWorkspacePanel
        sessionKey="websocket:chat-1"
        token="token"
        selectedPath={null}
        desktopWidth={560}
        onSelectPath={() => {}}
        onClose={() => {}}
      />,
    );

    await waitFor(() => expect(screen.getByText("No files in this workspace")).toBeInTheDocument());
    expect(screen.queryByText("Runtime Knowledge")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Knowledge graph preview")).not.toBeInTheDocument();
    expect(screen.getByTestId("document-workspace-panel")).toBeInTheDocument();
  });
});
