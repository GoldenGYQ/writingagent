import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { InteractionRequestCard } from "@/components/thread/InteractionRequestCard";
import type { InteractionRequestPayload } from "@/lib/types";

const request: InteractionRequestPayload = {
  id: "interaction-1",
  pending: true,
  status: "pending",
  kind: "form",
  reason: "outline_approval",
  title: "确认写作计划",
  prompt: "请选择综述的重点，再确认继续。",
  created_at: "2026-08-01T10:00:00+08:00",
  fields: [
    {
      id: "focus",
      type: "select",
      label: "重点方向",
      required: true,
      options: [
        { value: "runtime", label: "Runtime 架构" },
        { value: "memory", label: "上下文与记忆" },
      ],
    },
    {
      id: "notes",
      type: "textarea",
      label: "补充说明",
      required: false,
    },
  ],
  actions: [
    { id: "revise", label: "继续修改", style: "secondary" },
    { id: "confirm", label: "确认并继续", style: "primary" },
  ],
};

describe("InteractionRequestCard", () => {
  it("requires mandatory values and submits a structured response", () => {
    const onRespond = vi.fn();
    render(<InteractionRequestCard request={request} onRespond={onRespond} />);

    const confirm = screen.getByRole("button", { name: "确认并继续" });
    expect(confirm).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/重点方向/), { target: { value: "runtime" } });
    fireEvent.change(screen.getByLabelText("补充说明"), { target: { value: "补充持久化设计" } });
    expect(confirm).toBeEnabled();

    fireEvent.click(confirm);
    expect(onRespond).toHaveBeenCalledWith("confirm", {
      focus: "runtime",
      notes: "补充持久化设计",
    });
    expect(confirm).toBeEnabled();
  });

  it("explains the durable review boundary for a knowledge candidate", () => {
    render(
      <InteractionRequestCard
        request={{
          ...request,
          id: "interaction-evidence",
          fields: [],
          allow_message_response: true,
          accepts_attachments: true,
          response_scope: "knowledge_candidate",
        }}
        onRespond={vi.fn()}
      />,
    );

    expect(screen.getByText(/作为知识候选项/)).toBeInTheDocument();
    expect(screen.getByText(/审查通过后/)).toBeInTheDocument();
  });
});
