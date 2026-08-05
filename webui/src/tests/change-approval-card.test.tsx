import { fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vitest";

import { ChangeApprovalCard } from "@/components/thread/ChangeApprovalCard";
import type { InteractionRequestPayload } from "@/lib/types";

const request: InteractionRequestPayload = {
  id: "interaction-change-1",
  pending: true,
  kind: "change_approval",
  title: "Review proposed file changes",
  prompt: "write_file wants to change 1 file (+1/-0).",
  change: {
    tool: "write_file",
    added: 1,
    deleted: 0,
    files: [{
      path: "draft.md",
      absolute_path: "C:/project/draft.md",
      operation: "create",
      added: 1,
      deleted: 0,
      binary: false,
      diff: {
        format: "unified",
        text: "--- draft.md\n+++ draft.md\n@@ -0,0 +1 @@\n+hello",
      },
    }],
  },
};

describe("ChangeApprovalCard", () => {
  it("shows the proposed diff and submits server-owned actions without values", () => {
    const onRespond = vi.fn();
    render(<ChangeApprovalCard request={request} onRespond={onRespond} />);

    expect(screen.getByRole("heading", { name: "Review proposed file changes" })).toBeInTheDocument();
    fireEvent.click(screen.getByText("draft.md"));
    expect(screen.getByLabelText("draft.md diff")).toHaveTextContent("+hello");

    fireEvent.click(screen.getByRole("button", { name: "Apply once" }));
    expect(onRespond).toHaveBeenCalledWith("apply_once", {});
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(onRespond).toHaveBeenCalledWith("reject", {});
  });

  it("submits optional feedback when rejecting a writing ChangeSet", () => {
    const onRespond = vi.fn();
    render(
      <ChangeApprovalCard
        request={{
          ...request,
          id: "interaction-writing-change-1",
          fields: [{
            id: "feedback",
            type: "textarea",
            label: "Feedback for the next draft",
            required: false,
          }],
        }}
        onRespond={onRespond}
      />,
    );

    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "Keep the example but make the tone more formal." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(onRespond).toHaveBeenCalledWith("reject", {
      feedback: "Keep the example but make the tone more formal.",
    });
  });
});
