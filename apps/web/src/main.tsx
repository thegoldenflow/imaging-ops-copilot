import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { canSee, Layout, NAV } from "./components/Layout";
import { EmptyState, Loading } from "./components/ui";
import { AiUsagePage } from "./features/admin/AiUsagePage";
import { AuditPage } from "./features/admin/AuditPage";
import { BacklogPage } from "./features/backlog/BacklogPage";
import { CriticalPage } from "./features/critical/CriticalPage";
import { DosePage } from "./features/dose/DosePage";
import { InventoryPage } from "./features/inventory/InventoryPage";
import { PeerReviewPage } from "./features/peer-review/PeerReviewPage";
import { ContrastPage } from "./features/contrast/ContrastPage";
import { EvalsPage } from "./features/evals/EvalsPage";
import { MriSafetyPage } from "./features/mri-safety/MriSafetyPage";
import { MriQuestionnairePage } from "./features/patient/MriQuestionnairePage";
import { PrepPage } from "./features/prep/PrepPage";
import { PriorsPage } from "./features/priors/PriorsPage";
import { RequisitionDetailPage } from "./features/requisitions/RequisitionDetailPage";
import { RequisitionsPage } from "./features/requisitions/RequisitionsPage";
import { LoginPage } from "./features/auth/LoginPage";
import { FrontDeskPage } from "./features/frontdesk/FrontDeskPage";
import { HomePage } from "./features/home/HomePage";
import { PreRegPage } from "./features/patient/PreRegPage";
import { ReferrerPage } from "./features/referrer/ReferrerPage";
import { ReadingRoomPage } from "./features/reports/ReadingRoomPage";
import { ReportReviewPage } from "./features/reports/ReportReviewPage";
import { SchedulingPage } from "./features/scheduling/SchedulingPage";
import { AuthProvider, useAuth } from "./lib/auth";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});

function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <Loading label="Restoring session…" />;
  if (!user) return <Navigate to="/login" replace state={{ from: location }} />;
  return <>{children}</>;
}

function Guard({ path, children }: { path: string; children: ReactNode }) {
  const { user } = useAuth();
  const item = NAV.find((n) => n.to === path);
  if (user && item && !canSee(item, user.role)) {
    return <EmptyState title="Not available for your role" hint="Switch role from the header to open this page." />;
  }
  return <>{children}</>;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/prereg/:token" element={<PreRegPage />} />
            <Route path="/mri-screening/:token" element={<MriQuestionnairePage />} />
            <Route element={<RequireAuth><Layout /></RequireAuth>}>
              <Route index element={<HomePage />} />
              <Route path="/scheduling" element={<Guard path="/scheduling"><SchedulingPage /></Guard>} />
              <Route path="/front-desk" element={<Guard path="/front-desk"><FrontDeskPage /></Guard>} />
              <Route path="/reading" element={<Guard path="/reading"><ReadingRoomPage /></Guard>} />
              <Route path="/reading/:reportId" element={<Guard path="/reading"><ReportReviewPage /></Guard>} />
              <Route path="/my-reports" element={<Guard path="/my-reports"><ReferrerPage /></Guard>} />
              <Route path="/requisitions" element={<Guard path="/requisitions"><RequisitionsPage /></Guard>} />
              <Route path="/requisitions/:reqId" element={<Guard path="/requisitions"><RequisitionDetailPage /></Guard>} />
              <Route path="/contrast" element={<Guard path="/contrast"><ContrastPage /></Guard>} />
              <Route path="/mri-safety" element={<Guard path="/mri-safety"><MriSafetyPage /></Guard>} />
              <Route path="/prep" element={<Guard path="/prep"><PrepPage /></Guard>} />
              <Route path="/priors" element={<Guard path="/priors"><PriorsPage /></Guard>} />
              <Route path="/backlog" element={<Guard path="/backlog"><BacklogPage /></Guard>} />
              <Route path="/critical" element={<Guard path="/critical"><CriticalPage /></Guard>} />
              <Route path="/peer-review" element={<Guard path="/peer-review"><PeerReviewPage /></Guard>} />
              <Route path="/dose" element={<Guard path="/dose"><DosePage /></Guard>} />
              <Route path="/inventory" element={<Guard path="/inventory"><InventoryPage /></Guard>} />
              <Route path="/evals" element={<Guard path="/evals"><EvalsPage /></Guard>} />
              <Route path="/ai-usage" element={<Guard path="/ai-usage"><AiUsagePage /></Guard>} />
              <Route path="/audit" element={<Guard path="/audit"><AuditPage /></Guard>} />
              <Route path="*" element={<EmptyState title="Page not found" />} />
            </Route>
          </Routes>
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
