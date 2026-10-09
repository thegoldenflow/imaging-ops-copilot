import { useMutation } from "@tanstack/react-query";
import { MessageSquareText, ShieldX } from "lucide-react";
import { useState } from "react";
import { AiBadge, Badge, Button, Card } from "../../components/ui";
import { post } from "../../lib/api";
import type { TriageResult } from "./api";

const URGENCY_TONE = { routine: "slate", soon: "amber", urgent: "red" } as const;
const REASON_LABEL: Record<string, string> = {
  not_on_allow_list: "not on the agent's allow-list",
  unknown_tool: "no such tool",
  outside_data_scope: "outside the agent's data scope",
  invalid_arguments: "arguments not allowed",
  role_not_permitted: "your role may not cause it",
  consent_missing: "no patient consent",
  approval_required: "needs a person's approval first",
  approval_invalid: "no valid approval on file",
};

/** A message from the patient or family, triaged by the patient message triage agent (spec 6.4) for this nurse
 * or physician. The agent records it, routes it and drafts a reply; anything else the model asks for goes to the
 * Tool Gateway, which decides from the registry and the user's identity, never from the message. */
export function PatientMessageCard({ encounterId }: { encounterId: string }) {
  const [message, setMessage] = useState("");
  const [channel, setChannel] = useState<"portal" | "voicemail">("portal");
  const triage = useMutation({
    mutationFn: () => post<TriageResult>(`/api/hospital/encounters/${encounterId}/patient-message`, { message, channel }),
  });
  const r = triage.data;
  const refused = r?.tool_requests.filter((t) => t.status !== "ok" && t.status !== "replayed") ?? [];
  return (
    <Card title={<span className="flex items-center gap-2"><MessageSquareText className="size-4" /> Patient message</span>} className="mt-4">
      <div className="space-y-2">
        <label className="block text-xs font-medium text-slate-600" htmlFor="patient-message">Message from the patient or family</label>
        <textarea
          id="patient-message"
          className="h-20 w-full rounded-lg border border-slate-300 p-2 text-sm focus:border-brand-500 focus:outline-none"
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          placeholder="Paste a portal message or a voicemail transcript"
        />
        <div className="flex flex-wrap items-center gap-2">
          <select className="h-8 rounded-lg border border-slate-300 px-2 text-xs" value={channel} onChange={(e) => setChannel(e.target.value as "portal" | "voicemail")} aria-label="Channel">
            <option value="portal">Patient portal</option>
            <option value="voicemail">Voicemail transcript</option>
          </select>
          <Button size="sm" variant="ai" disabled={!message.trim()} loading={triage.isPending} onClick={() => triage.mutate()}>
            Triage with AI
          </Button>
          <span className="text-xs text-slate-500">The message is recorded; a nurse reads every message and reply.</span>
        </div>
      </div>
      {triage.error && <p className="mt-2 text-sm text-rose-600" role="alert">{triage.error.message}</p>}
      {r && (
        <div className="mt-3 space-y-2 rounded-lg border border-slate-200 p-3 text-sm" data-testid="triage-result">
          {r.status === "not_processed" ? (
            <p className="text-slate-700">No AI triage ({r.reason}). The message is recorded and a nurse task asks to read it.</p>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <AiBadge label="AI triage" agent="patient_message_triage" />
                {r.urgency && <Badge tone={URGENCY_TONE[r.urgency]}>{r.urgency}</Badge>}
                {r.category && <Badge>{r.category}</Badge>}
                {r.red_flags.map((f) => <Badge key={f} tone="red">red flag: {f}</Badge>)}
              </div>
              {r.summary && <p className="text-slate-800">{r.summary}</p>}
              {r.reply_draft && (
                <div>
                  <p className="text-xs font-medium text-slate-500">Reply draft (in your review queue)</p>
                  <p className="whitespace-pre-wrap rounded bg-slate-50 p-2 text-slate-700">{r.reply_draft}</p>
                </div>
              )}
              <p className="text-xs text-slate-500">
                Recorded as Communication/{r.communication_id}
                {r.task_id && <> · nurse task Task/{r.task_id}</>}
                {r.review_task_id && <> · review Task/{r.review_task_id}</>} · trace {r.run_id}
              </p>
            </>
          )}
          {refused.length > 0 && (
            <div className="rounded-lg bg-rose-50 p-2" data-testid="refused-requests">
              <p className="flex items-center gap-1 text-xs font-semibold text-rose-700"><ShieldX className="size-3.5" /> Refused by the tool gateway</p>
              <ul className="mt-1 space-y-0.5 text-xs text-rose-800">
                {refused.map((t, i) => (
                  <li key={i}><code>{t.tool_id}</code>: {REASON_LABEL[t.reason ?? ""] ?? t.reason}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
