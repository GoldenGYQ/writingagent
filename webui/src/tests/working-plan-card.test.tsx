import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { WorkingPlanCard } from "@/components/thread/WorkingPlanCard";
import type { WorkingPlanPayload } from "@/lib/types";

const activePlan: WorkingPlanPayload = {
  id: "plan-1",
  version: 1,
  kind: "writing",
  status: "active",
  title: "完成技术综述",
  objective: "形成可投稿的综述稿件",
  steps: [
    { id: "scope", title: "明确综述范围", status: "completed" },
    { id: "outline", title: "建立章节结构", status: "in_progress" },
    { id: "sources", title: "整理参考资料", status: "pending" },
    { id: "draft", title: "撰写正文", status: "pending" },
  ],
};

describe("WorkingPlanCard", () => {
  it("shows every step while active and keeps a completed plan as a collapsed receipt", () => {
    const onDismiss = vi.fn();
    const { rerender } = render(<WorkingPlanCard plan={activePlan} onDismiss={onDismiss} />);

    expect(screen.getByText("明确综述范围")).toBeInTheDocument();
    expect(screen.getByText("建立章节结构")).toBeInTheDocument();
    expect(screen.getByText("整理参考资料")).toBeInTheDocument();
    expect(screen.getByText("撰写正文")).toBeInTheDocument();
    expect(screen.getByText("执行中 · 1/4 个步骤")).toBeInTheDocument();

    const completedPlan: WorkingPlanPayload = {
      ...activePlan,
      version: 2,
      status: "completed",
      steps: activePlan.steps?.map((step) => ({ ...step, status: "completed" as const })),
    };
    rerender(<WorkingPlanCard plan={completedPlan} onDismiss={onDismiss} />);

    expect(screen.getByText("已完成 · 4/4 个步骤")).toBeInTheDocument();
    expect(screen.queryByText("明确综述范围")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { expanded: false }));
    expect(screen.getByText("明确综述范围")).toBeInTheDocument();
    expect(screen.getAllByText("已完成")).toHaveLength(4);

    fireEvent.click(screen.getByRole("button", { name: "关闭计划" }));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });
});
