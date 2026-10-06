import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ErrorState, Loading, PageHeader, Tabs } from "../../components/ui";
import { api } from "../../lib/api";
import type { BackfillCase, Dashboard, Meta } from "../../lib/types";
import { AppointmentsTab } from "./AppointmentsTab";
import { BackfillTab } from "./BackfillTab";
import { Overview } from "./Overview";
import { SettingsTab, WaitlistTab } from "./SettingsTab";

type Tab = "overview" | "appointments" | "backfill" | "waitlist" | "settings";

export function SchedulingPage() {
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as Tab) || "overview";
  const setTab = (t: Tab) => setParams({ tab: t }, { replace: true });
  const [focusCase, setFocusCase] = useState<string | null>(null);
  const meta = useQuery({ queryKey: ["meta"], queryFn: () => api<Meta>("/api/meta") });
  // Polling keeps the board live: any booking or cancellation shows up within seconds.
  const dashboard = useQuery({
    queryKey: ["dashboard"],
    queryFn: () => api<Dashboard>("/api/scheduling/dashboard"),
    refetchInterval: 3000,
  });
  const siteNames = Object.fromEntries((meta.data?.sites ?? []).map((s) => [s.id, s.name]));

  const openBackfill = (c: BackfillCase) => {
    setFocusCase(c.id);
    setTab("backfill");
  };

  return (
    <div>
      <PageHeader title="Scheduling command center" subtitle="Live utilization, cancellation backfill, cross-site balancing and no-show risk." />
      <Tabs
        value={tab}
        onChange={setTab}
        tabs={[
          { id: "overview", label: "Overview" },
          { id: "appointments", label: "Appointments" },
          { id: "backfill", label: <>Backfill{dashboard.data?.kpis.open_backfill_cases ? <span className="ml-1.5 rounded-full bg-rose-600 px-1.5 text-xs text-white">{dashboard.data.kpis.open_backfill_cases}</span> : null}</> },
          { id: "waitlist", label: "Waitlist" },
          { id: "settings", label: "Settings & model" },
        ]}
      />
      {tab === "overview" && (
        <>
          {dashboard.isLoading && <Loading label="Loading dashboard…" />}
          {dashboard.error && <ErrorState error={dashboard.error} onRetry={() => dashboard.refetch()} />}
          {dashboard.data && <Overview dashboard={dashboard.data} siteNames={siteNames} />}
        </>
      )}
      {tab === "appointments" && <AppointmentsTab sites={meta.data?.sites ?? []} onBackfill={openBackfill} />}
      {tab === "backfill" && <BackfillTab focusId={focusCase} />}
      {tab === "waitlist" && <WaitlistTab />}
      {tab === "settings" && <SettingsTab />}
    </div>
  );
}
