import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Mail, MessageSquare, Phone } from "lucide-react";
import { Fragment, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { AiBadge, Badge, Button, Card, EmptyState, ErrorState, Loading, PageHeader, Tabs } from "../../components/ui";
import { api, post } from "../../lib/api";
import { dateTime, LANGUAGE_LABEL } from "../../lib/format";
import type { CallSession, Message } from "../../lib/types";
import { PhoneAgent } from "./PhoneAgent";

type Tab = "phone" | "outbox" | "prereg" | "calls";

const CHANNEL_ICON = { sms: MessageSquare, email: Mail, phone: Phone } as const;
const STATUS_TONE: Record<string, "green" | "amber" | "red" | "slate"> = { sent: "green", scheduled: "amber", failed: "red", cancelled: "slate" };

/** Turns the pre-registration path inside a message into a clickable link. */
function withLinks(body: string): ReactNode {
  const parts = body.split(/(\/prereg\/[\w-]+)/);
  return parts.map((p, i) =>
    p.startsWith("/prereg/") ? (
      <a key={i} href={p} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 font-medium text-brand-700 underline">
        {p}<ExternalLink className="size-3" />
      </a>
    ) : (
      <Fragment key={i}>{p}</Fragment>
    ),
  );
}

function Outbox() {
  const queryClient = useQueryClient();
  const q = useQuery({ queryKey: ["outbox"], queryFn: () => api<{ messages: Message[]; total: number }>("/api/frontdesk/outbox?limit=80"), refetchInterval: 3000 });
  const sendNow = useMutation({ mutationFn: (id: string) => post(`/api/frontdesk/outbox/${id}/send-now`), onSuccess: () => queryClient.invalidateQueries() });
  const reply = useMutation({
    mutationFn: ({ id, text }: { id: string; text: string }) => post<{ action: string; detail: string }>(`/api/frontdesk/outbox/${id}/reply`, { text }),
    onSuccess: () => queryClient.invalidateQueries(),
  });

  return (
    <Card title="Patient messages (mock SMS and email)" padded={false} actions={q.data && <span className="text-xs text-slate-500">{q.data.total} total</span>}>
      {q.isLoading && <Loading />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data?.messages.length === 0 && <EmptyState title="No messages yet" />}
      {reply.data && <p className="border-b border-slate-100 px-4 py-2 text-sm text-slate-600">Patient reply processed: {reply.data.detail}</p>}
      {q.data && q.data.messages.length > 0 && (
        <ul className="divide-y divide-slate-100">
          {q.data.messages.map((m) => {
            const Icon = CHANNEL_ICON[m.channel as keyof typeof CHANNEL_ICON] ?? MessageSquare;
            const canReply = m.channel === "sms" && m.status === "sent" && ["reminder_72h", "reminder_24h", "reminder_extra", "booking_confirmation", "waitlist_offer"].includes(m.kind);
            return (
              <li key={m.id} className="flex gap-3 px-4 py-3" data-testid={`msg-${m.kind}`}>
                <Icon className="mt-0.5 size-4 shrink-0 text-slate-400" />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <span className="font-medium text-slate-800">{m.patient_name ?? m.to}</span>
                    <Badge>{m.kind.replaceAll("_", " ")}</Badge>
                    <Badge tone="blue">{LANGUAGE_LABEL[m.language] ?? m.language}</Badge>
                    <Badge tone={STATUS_TONE[m.status]}>{m.status === "scheduled" ? `scheduled ${dateTime(m.scheduled_for)}` : m.status}</Badge>
                  </div>
                  <p className="mt-1 text-sm break-words text-slate-700">{withLinks(m.body)}</p>
                </div>
                <div className="flex shrink-0 items-start gap-1">
                  {m.status === "scheduled" && <Button size="sm" variant="ghost" onClick={() => sendNow.mutate(m.id)} title="Demo: deliver now">Send now</Button>}
                  {canReply && m.kind === "waitlist_offer" && <Button size="sm" variant="ghost" onClick={() => reply.mutate({ id: m.id, text: "YES" })}>Reply YES</Button>}
                  {canReply && m.kind !== "waitlist_offer" && (
                    <>
                      <Button size="sm" variant="ghost" onClick={() => reply.mutate({ id: m.id, text: "C" })} title="Demo: patient confirms">Reply C</Button>
                      <Button size="sm" variant="ghost" onClick={() => reply.mutate({ id: m.id, text: "X" })} title="Demo: patient cancels">Reply X</Button>
                    </>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

interface PreReg {
  token: string;
  patient_name: string;
  exam_name: string | null;
  start: string | null;
  status: string;
  insurance_type: string | null;
  coverage: { valid: boolean; status: string; detail: string } | null;
  submitted_at: string | null;
}

function PreRegs() {
  const q = useQuery({ queryKey: ["preregs"], queryFn: () => api<{ preregistrations: PreReg[] }>("/api/frontdesk/preregistrations"), refetchInterval: 4000 });
  return (
    <Card title="Online pre-registration" padded={false}>
      {q.isLoading && <Loading />}
      {q.error && <ErrorState error={q.error} />}
      {q.data?.preregistrations.length === 0 && <EmptyState title="No pre-registration links yet" hint="A link is sent automatically with every new booking." />}
      {q.data && q.data.preregistrations.length > 0 && (
        <table className="w-full text-left text-sm">
          <thead className="bg-slate-50 text-xs text-slate-500"><tr>{["Patient", "Exam", "Status", "Insurance check", "Link"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr></thead>
          <tbody className="divide-y divide-slate-100">
            {q.data.preregistrations.map((p) => (
              <tr key={p.token}>
                <td className="px-3 py-2 font-medium">{p.patient_name}</td>
                <td className="px-3 py-2">{p.exam_name}<p className="text-xs text-slate-500">{p.start && dateTime(p.start)}</p></td>
                <td className="px-3 py-2"><Badge tone={p.status === "completed" ? "green" : "amber"}>{p.status}</Badge></td>
                <td className="px-3 py-2">{p.coverage ? <Badge tone={p.coverage.valid ? "green" : "red"}>{p.insurance_type?.toUpperCase()} · {p.coverage.status}</Badge> : "–"}</td>
                <td className="px-3 py-2"><a href={`/prereg/${p.token}`} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-brand-700 underline">Open as patient <ExternalLink className="size-3" /></a></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function CallLog() {
  const q = useQuery({ queryKey: ["calls"], queryFn: () => api<{ calls: CallSession[] }>("/api/frontdesk/calls"), refetchInterval: 5000 });
  return (
    <Card title="Call log" padded={false}>
      {q.isLoading && <Loading />}
      {q.error && <ErrorState error={q.error} />}
      {q.data?.calls.length === 0 && <EmptyState title="No calls yet" />}
      <ul className="divide-y divide-slate-100">
        {q.data?.calls.map((c) => (
          <li key={c.id} className="px-4 py-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{c.patient_name ?? "Unverified caller"}</span>
              <span className="text-xs text-slate-500">{dateTime(c.started_at)} · {c.transcript.filter((t) => t.role === "caller").length} caller turns</span>
              <Badge tone={c.outcome === "transferred" ? "amber" : c.outcome === "resolved" ? "green" : "slate"}>{c.outcome}</Badge>
            </div>
            {c.summary && <p className="mt-1 flex items-start gap-2 text-slate-600"><AiBadge label="Summary" agent="call_summary" />{c.summary}</p>}
          </li>
        ))}
      </ul>
    </Card>
  );
}

export function FrontDeskPage() {
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as Tab) || "phone";
  return (
    <div>
      <PageHeader title="Front desk" subtitle="AI phone receptionist, automatic reminders, online pre-registration and insurance checks." />
      <Tabs value={tab} onChange={(t) => setParams({ tab: t }, { replace: true })} tabs={[
        { id: "phone", label: "Phone agent" },
        { id: "outbox", label: "Messages" },
        { id: "prereg", label: "Pre-registration" },
        { id: "calls", label: "Call log" },
      ]} />
      {tab === "phone" && <PhoneAgent />}
      {tab === "outbox" && <Outbox />}
      {tab === "prereg" && <PreRegs />}
      {tab === "calls" && <CallLog />}
    </div>
  );
}
