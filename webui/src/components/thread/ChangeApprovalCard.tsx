import { useEffect, useState } from "react";
import { ChevronRight, FileDiff, ShieldCheck } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { InteractionRequestPayload } from "@/lib/types";

interface ChangeApprovalCardProps {
  request: InteractionRequestPayload;
  onRespond: (action: string, values: Record<string, unknown>) => void;
}

export function ChangeApprovalCard({ request, onRespond }: ChangeApprovalCardProps) {
  const { t } = useTranslation();
  const change = request.change;
  const files = change?.files ?? [];
  const titleId = `change-approval-${request.id ?? "pending"}`;
  const [feedback, setFeedback] = useState("");
  const hasFeedbackField = (request.fields ?? []).some((field) => field.id === "feedback");

  useEffect(() => {
    setFeedback("");
  }, [request.id]);

  return (
    <section
      aria-labelledby={titleId}
      className="mx-auto mb-3 w-full max-w-3xl rounded-2xl border border-amber-500/25 bg-card p-4 shadow-sm"
    >
      <div className="flex items-start gap-3">
        <div className="mt-0.5 rounded-full bg-amber-500/10 p-2 text-amber-700 dark:text-amber-300">
          <ShieldCheck className="h-4 w-4" aria-hidden />
        </div>
        <div className="min-w-0 flex-1">
          <h3 id={titleId} className="text-sm font-semibold">
            {request.title || t("thread.changeApproval.title", { defaultValue: "Review proposed file changes" })}
          </h3>
          <p className="mt-1 text-sm text-muted-foreground">
            {request.prompt || t("thread.changeApproval.prompt", { defaultValue: "No file has been changed yet." })}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span>{t("thread.changeApproval.files", { count: files.length, defaultValue: `${files.length} files` })}</span>
            <span className="font-mono text-emerald-600">+{change?.added ?? 0}</span>
            <span className="font-mono text-rose-600">-{change?.deleted ?? 0}</span>
          </div>
        </div>
      </div>

      <div className="mt-4 space-y-2">
        {files.map((file) => (
          <details key={file.absolute_path ?? file.path} className="group rounded-xl border border-border/65 bg-muted/20">
            <summary className="flex cursor-pointer list-none items-center gap-2 rounded-xl px-3 py-2 text-xs font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              <ChevronRight className="h-3.5 w-3.5 shrink-0 transition-transform group-open:rotate-90 motion-reduce:transition-none" aria-hidden />
              <FileDiff className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
              <span className="min-w-0 flex-1 truncate font-mono">{file.path}</span>
              <span className="font-mono text-emerald-600">+{file.added ?? 0}</span>
              <span className="font-mono text-rose-600">-{file.deleted ?? 0}</span>
            </summary>
            <div className="border-t border-border/55">
              {file.diff?.text ? (
                <pre className="max-h-80 overflow-auto p-3 text-[11px] leading-5" aria-label={`${file.path} diff`}>
                  <code>{file.diff.text}</code>
                </pre>
              ) : (
                <p className="p-3 text-xs text-muted-foreground">
                  {file.binary
                    ? t("thread.changeApproval.binary", { defaultValue: "Binary or oversized file; textual diff is unavailable." })
                    : t("thread.changeApproval.noDiff", { defaultValue: "No textual diff is available." })}
                </p>
              )}
              {file.diff?.truncated ? (
                <p className="border-t border-border/55 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
                  {t("thread.changeApproval.truncated", { defaultValue: "Diff preview is truncated." })}
                </p>
              ) : null}
            </div>
          </details>
        ))}
      </div>

      <p className="mt-3 text-xs text-muted-foreground" aria-live="polite">
        {t("thread.changeApproval.notApplied", { defaultValue: "The proposal will be revalidated against the current file hashes before it is applied." })}
      </p>
      {hasFeedbackField ? (
        <label className="mt-3 block space-y-1.5 text-sm" htmlFor={`${titleId}-feedback`}>
          <span className="font-medium">Feedback for the next draft</span>
          <span className="block text-xs text-muted-foreground">
            Optional. Rejection keeps the file unchanged and gives the Agent this feedback for its next proposal.
          </span>
          <Textarea
            id={`${titleId}-feedback`}
            value={feedback}
            onChange={(event) => setFeedback(event.target.value)}
            placeholder="例如：保留原句，但把这一段改成更正式的表述"
            rows={3}
          />
        </label>
      ) : null}
      <div className="mt-4 flex flex-wrap justify-end gap-2">
        <Button type="button" variant="outline" onClick={() => onRespond("reject", hasFeedbackField ? { feedback } : {})}>
          {t("thread.changeApproval.reject", { defaultValue: "Reject" })}
        </Button>
        <Button type="button" onClick={() => onRespond("apply_once", {})}>
          {t("thread.changeApproval.applyOnce", { defaultValue: "Apply once" })}
        </Button>
      </div>
    </section>
  );
}
