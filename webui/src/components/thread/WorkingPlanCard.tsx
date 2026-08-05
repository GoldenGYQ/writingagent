import { useEffect, useMemo, useState } from "react";
import {
  Check,
  ChevronDown,
  Circle,
  CircleAlert,
  LoaderCircle,
  Minus,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import type { WorkingPlanPayload, WorkingPlanStepStatus } from "@/lib/types";

interface WorkingPlanCardProps {
  plan: WorkingPlanPayload;
  onDismiss: () => void;
}

const STATUS_LABELS: Record<WorkingPlanStepStatus, string> = {
  pending: "待处理",
  in_progress: "进行中",
  completed: "已完成",
  blocked: "受阻",
  skipped: "已跳过",
};

function isTerminal(status: string | undefined): boolean {
  return status === "completed" || status === "cancelled";
}

function StepStatusIcon({ status }: { status: WorkingPlanStepStatus }) {
  const className = "h-4 w-4 shrink-0";
  if (status === "completed") return <Check className={`${className} text-emerald-600`} />;
  if (status === "in_progress") {
    return <LoaderCircle className={`${className} animate-spin text-primary`} />;
  }
  if (status === "blocked") return <CircleAlert className={`${className} text-destructive`} />;
  if (status === "skipped") return <Minus className={`${className} text-muted-foreground`} />;
  return <Circle className={`${className} text-muted-foreground/60`} />;
}

export function WorkingPlanCard({ plan, onDismiss }: WorkingPlanCardProps) {
  const terminal = isTerminal(plan.status);
  const [expanded, setExpanded] = useState(!terminal);
  const steps = plan.steps ?? [];
  const completed = steps.filter((step) => step.status === "completed").length;
  const progress = steps.length > 0 ? Math.round((completed / steps.length) * 100) : 0;
  const planIdentity = `${plan.id ?? "plan"}:${plan.version ?? 0}`;

  useEffect(() => {
    // Live plans expose their steps by default. A terminal update remains
    // visible as a compact receipt until dismissed or the next query starts.
    setExpanded(!terminal);
  }, [planIdentity, terminal]);

  const statusText = useMemo(() => {
    if (plan.status === "completed") return "已完成";
    if (plan.status === "cancelled") return "已取消";
    if (plan.status === "waiting_for_user") return "等待确认";
    return "执行中";
  }, [plan.status]);

  return (
    <section
      className="mx-auto mb-3 w-full max-w-3xl overflow-hidden rounded-2xl border bg-card/95 shadow-sm"
      data-testid="working-plan-card"
    >
      <div className="flex min-w-0 items-center gap-1 px-3 py-2.5">
        <button
          type="button"
          className="flex min-w-0 flex-1 items-center gap-2 rounded-lg px-1 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          aria-expanded={expanded}
          aria-controls={`working-plan-steps-${plan.id ?? "current"}`}
          onClick={() => setExpanded((value) => !value)}
        >
          <ChevronDown
            aria-hidden="true"
            className={`h-4 w-4 shrink-0 text-muted-foreground transition-transform ${expanded ? "rotate-0" : "-rotate-90"}`}
          />
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-medium text-foreground">
              {plan.kind === "writing" ? "Writing Plan" : "Working Plan"}
              {plan.title ? ` · ${plan.title}` : ""}
            </span>
            <span className="mt-0.5 block text-xs text-muted-foreground">
              {statusText} · {completed}/{steps.length} 个步骤
            </span>
          </span>
        </button>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="h-8 w-8 shrink-0 rounded-full text-muted-foreground"
          aria-label="关闭计划"
          onClick={onDismiss}
        >
          <X className="h-4 w-4" />
        </Button>
      </div>

      <div className="h-1 bg-muted" aria-hidden="true">
        <div
          className={`h-full transition-[width] duration-300 ${terminal ? "bg-emerald-500" : "bg-primary"}`}
          style={{ width: `${progress}%` }}
        />
      </div>

      {expanded ? (
        <div
          id={`working-plan-steps-${plan.id ?? "current"}`}
          className="border-t px-4 py-3"
        >
          {plan.objective ? (
            <p className="mb-3 text-xs leading-5 text-muted-foreground">{plan.objective}</p>
          ) : null}
          <ol className="space-y-2.5">
            {steps.map((step, index) => (
              <li key={step.id || index} className="flex items-start gap-2.5 text-sm">
                <span className="mt-0.5" aria-hidden="true">
                  <StepStatusIcon status={step.status} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className={step.status === "completed" ? "text-muted-foreground line-through" : "text-foreground"}>
                    {step.title}
                  </span>
                  {step.description ? (
                    <span className="mt-0.5 block text-xs leading-5 text-muted-foreground">
                      {step.description}
                    </span>
                  ) : null}
                </span>
                <span className="shrink-0 text-[11px] text-muted-foreground">
                  {STATUS_LABELS[step.status]}
                </span>
              </li>
            ))}
          </ol>
        </div>
      ) : null}
    </section>
  );
}
