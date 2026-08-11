import { useEffect, useMemo, useState } from "react";
import { CircleHelp } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type { InteractionField, InteractionRequestPayload } from "@/lib/types";

interface InteractionRequestCardProps {
  request: InteractionRequestPayload;
  onRespond: (action: string, values: Record<string, unknown>) => void;
}

function emptyValue(field: InteractionField): unknown {
  return field.type === "checkbox" ? [] : field.type === "confirm" ? false : "";
}

export function InteractionRequestCard({ request, onRespond }: InteractionRequestCardProps) {
  const fields = request.fields ?? [];
  const [values, setValues] = useState<Record<string, unknown>>({});

  useEffect(() => {
    setValues(Object.fromEntries(fields.map((field) => [field.id, emptyValue(field)])));
  }, [request.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const missingRequired = useMemo(() => fields.some((field) => {
    if (!field.required) return false;
    const value = values[field.id];
    return value === undefined || value === "" || value === false
      || (Array.isArray(value) && value.length === 0);
  }), [fields, values]);

  const setValue = (id: string, value: unknown) => {
    setValues((current) => ({ ...current, [id]: value }));
  };

  return (
    <section className="mx-auto mb-3 w-full max-w-3xl rounded-2xl border border-primary/25 bg-card p-4 shadow-sm">
      <div className="mb-3 flex items-start gap-3">
        <div className="mt-0.5 rounded-full bg-primary/10 p-2 text-primary">
          <CircleHelp className="h-4 w-4" />
        </div>
        <div>
          <h3 className="text-sm font-semibold">{request.title || "需要你的确认"}</h3>
          {request.prompt ? <p className="mt-1 text-sm text-muted-foreground">{request.prompt}</p> : null}
          {request.allow_message_response ? (
            <p className="mt-2 rounded-lg border border-dashed border-primary/25 bg-primary/5 px-3 py-2 text-xs text-muted-foreground">
              可直接在下方输入框回复{request.accepts_attachments ? "，或拖入文档、图片等补充材料" : ""}；发送后任务会自动继续。
              {request.response_scope === "knowledge_candidate"
                ? " 材料将作为知识候选项，审查通过后才会发布到知识库。"
                : " 材料默认只用于当前任务，不会自动写入长期知识库。"}
            </p>
          ) : null}
        </div>
      </div>

      <div className="space-y-3">
        {fields.map((field) => (
          <label key={field.id} className="block space-y-1.5 text-sm">
            <span className="font-medium">
              {field.label}{field.required ? <span className="text-destructive"> *</span> : null}
            </span>
            {field.description ? <span className="block text-xs text-muted-foreground">{field.description}</span> : null}
            {field.type === "textarea" ? (
              <Textarea
                value={String(values[field.id] ?? "")}
                onChange={(event) => setValue(field.id, event.target.value)}
              />
            ) : field.type === "select" || field.type === "radio" ? (
              <select
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                value={String(values[field.id] ?? "")}
                onChange={(event) => setValue(field.id, event.target.value)}
              >
                <option value="">请选择</option>
                {(field.options ?? []).map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
            ) : field.type === "checkbox" ? (
              <div className="space-y-1.5">
                {(field.options ?? []).map((option) => {
                  const selected = Array.isArray(values[field.id]) ? values[field.id] as unknown[] : [];
                  return (
                    <label key={option.value} className="flex items-center gap-2 font-normal">
                      <input
                        type="checkbox"
                        checked={selected.includes(option.value)}
                        onChange={(event) => setValue(
                          field.id,
                          event.target.checked
                            ? [...selected, option.value]
                            : selected.filter((value) => value !== option.value),
                        )}
                      />
                      {option.label}
                    </label>
                  );
                })}
              </div>
            ) : field.type === "confirm" ? (
              <label className="flex items-center gap-2 font-normal">
                <input
                  type="checkbox"
                  checked={values[field.id] === true}
                  onChange={(event) => setValue(field.id, event.target.checked)}
                />
                我已阅读并确认
              </label>
            ) : (
              <Input
                value={String(values[field.id] ?? "")}
                onChange={(event) => setValue(field.id, event.target.value)}
              />
            )}
          </label>
        ))}
      </div>

      <div className="mt-4 flex flex-wrap justify-end gap-2">
        {(request.actions ?? []).map((action) => (
          <Button
            key={action.id}
            type="button"
            variant={action.style === "primary" ? "default" : action.style === "danger" ? "destructive" : "outline"}
            disabled={action.style === "primary" && missingRequired}
            onClick={() => {
              onRespond(action.id, values);
            }}
          >
            {action.label}
          </Button>
        ))}
      </div>
    </section>
  );
}
