import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Sparkles, Upload } from "lucide-react";
import { useRef } from "react";
import { useNavigate } from "react-router-dom";
import { Badge, Button, Card, EmptyState, ErrorState, Loading, PageHeader } from "../../components/ui";
import { api, getToken, post } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { dateTime } from "../../lib/format";
import type { Report, Study } from "../../lib/types";

const STATUS_TONE = { unreported: "amber", draft: "ai", signed: "green" } as const;

export function ReadingRoomPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const worklist = useQuery({ queryKey: ["worklist"], queryFn: () => api<{ studies: Study[] }>("/api/reports/worklist") });
  const isRadiologist = user?.role === "radiologist";

  const draft = useMutation({
    mutationFn: (studyId: string) => post<Report>(`/api/reports/studies/${studyId}/draft`),
    onSuccess: (report) => {
      queryClient.invalidateQueries({ queryKey: ["worklist"] });
      navigate(`/reading/${report.id}`);
    },
  });

  const upload = useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch("/api/reports/upload", { method: "POST", body: form, headers: { Authorization: `Bearer ${getToken()}` } });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Upload failed");
      return (await res.json()) as Study;
    },
    onSuccess: (study) => draft.mutate(study.id),
  });

  return (
    <div>
      <PageHeader
        title="Reading room"
        subtitle="Chest X-ray worklist. AI drafts a preliminary report; you review every section and sign."
        actions={
          isRadiologist && (
            <>
              <input ref={fileInput} type="file" accept="image/png,image/jpeg" className="hidden"
                onChange={(e) => { const f = e.target.files?.[0]; if (f) upload.mutate(f); e.target.value = ""; }} />
              <Button onClick={() => fileInput.current?.click()} loading={upload.isPending}>
                <Upload className="size-4" /> Upload image
              </Button>
            </>
          )
        }
      />
      {(draft.error || upload.error) && <p className="mb-3 text-sm text-rose-600" role="alert">{((draft.error || upload.error) as Error).message}</p>}
      <Card padded={false}>
        {worklist.isLoading && <Loading />}
        {worklist.error && <ErrorState error={worklist.error} onRetry={() => worklist.refetch()} />}
        {worklist.data?.studies.length === 0 && <EmptyState title="Worklist is empty" />}
        {worklist.data && worklist.data.studies.length > 0 && (
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50 text-xs text-slate-500">
              <tr>{["Performed", "Patient", "Exam", "Indication", "Referrer", "Status", ""].map((h, i) => <th key={i} className="px-3 py-2 font-medium">{h}</th>)}</tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {worklist.data.studies.map((s) => (
                <tr key={s.id} data-testid={`study-${s.id}`}>
                  <td className="px-3 py-2 whitespace-nowrap">{dateTime(s.performed_at)}</td>
                  <td className="px-3 py-2">
                    <p className="font-medium">{s.patient_name}</p>
                    <p className="text-xs text-slate-500">{s.patient_age} y · {s.patient_sex}</p>
                  </td>
                  <td className="px-3 py-2">{s.exam_name}</td>
                  <td className="px-3 py-2 text-slate-600">{s.indication}</td>
                  <td className="px-3 py-2 text-slate-600">{s.referrer_name}</td>
                  <td className="px-3 py-2"><Badge tone={STATUS_TONE[s.report_status as keyof typeof STATUS_TONE] ?? "slate"}>{s.report_status}</Badge></td>
                  <td className="px-3 py-2 text-right">
                    {s.report_id ? (
                      <Button size="sm" onClick={() => navigate(`/reading/${s.report_id}`)}>Open</Button>
                    ) : isRadiologist ? (
                      <Button size="sm" variant="ai" loading={draft.isPending && draft.variables === s.id} onClick={() => draft.mutate(s.id)}>
                        <Sparkles className="size-3.5" /> Draft with AI
                      </Button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      <p className="mt-3 text-xs text-slate-500">
        Demo images are synthetic drawings, not real radiographs. With an API key, uploaded PNG/JPEG images are sent to Claude after de-identification.
      </p>
    </div>
  );
}
