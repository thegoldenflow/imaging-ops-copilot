import clsx from "clsx";
import {
  Activity,
  CalendarClock,
  ClipboardList,
  Droplet,
  FileText,
  FlaskConical,
  History,
  Home,
  Inbox,
  Languages,
  LogOut,
  Magnet,
  PhoneCall,
  RotateCcw,
  ScanLine,
  ShieldCheck,
  Siren,
  Sparkles,
} from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, post } from "../lib/api";
import { useAuth } from "../lib/auth";
import { ROLE_LABEL, type Meta, type Role } from "../lib/types";
import { Badge, Button } from "./ui";

interface NavItem {
  to: string;
  label: string;
  icon: typeof Home;
  roles: Role[] | "all";
  section: "" | "Operations" | "Intake pipeline" | "Radiology ops" | "Oversight";
}

const STAFF: Role[] = ["front_desk", "technologist", "radiologist", "operations_manager", "medical_director", "admin"];

export const NAV: NavItem[] = [
  { to: "/", label: "Home", icon: Home, roles: "all", section: "" },
  { to: "/scheduling", label: "Scheduling", icon: CalendarClock, roles: ["front_desk", "technologist", "operations_manager", "medical_director", "admin"], section: "Operations" },
  { to: "/front-desk", label: "Front desk", icon: PhoneCall, roles: ["front_desk", "operations_manager", "admin"], section: "Operations" },
  { to: "/reading", label: "Reading room", icon: ScanLine, roles: ["radiologist", "medical_director", "admin"], section: "Operations" },
  { to: "/my-reports", label: "My reports", icon: FileText, roles: ["referrer"], section: "Operations" },
  { to: "/requisitions", label: "Requisitions", icon: ClipboardList, roles: STAFF, section: "Intake pipeline" },
  { to: "/contrast", label: "Contrast checks", icon: Droplet, roles: ["technologist", "radiologist", "medical_director", "admin"], section: "Intake pipeline" },
  { to: "/mri-safety", label: "MRI safety", icon: Magnet, roles: ["technologist", "radiologist", "medical_director", "admin"], section: "Intake pipeline" },
  { to: "/prep", label: "Prep instructions", icon: Languages, roles: ["front_desk", "radiologist", "medical_director", "admin"], section: "Intake pipeline" },
  { to: "/priors", label: "Prior imaging", icon: History, roles: ["front_desk", "technologist", "radiologist", "operations_manager", "admin"], section: "Intake pipeline" },
  { to: "/backlog", label: "Reading backlog", icon: Inbox, roles: ["radiologist", "operations_manager", "medical_director", "admin"], section: "Radiology ops" },
  { to: "/critical", label: "Critical results", icon: Siren, roles: ["front_desk", "radiologist", "operations_manager", "medical_director", "admin"], section: "Radiology ops" },
  { to: "/ai-usage", label: "AI usage", icon: Activity, roles: ["operations_manager", "medical_director", "admin"], section: "Oversight" },
  { to: "/evals", label: "AI evaluations", icon: FlaskConical, roles: ["radiologist", "operations_manager", "medical_director", "admin"], section: "Oversight" },
  { to: "/audit", label: "Audit log", icon: ShieldCheck, roles: ["medical_director", "admin"], section: "Oversight" },
];

export function canSee(item: NavItem, role: Role) {
  return item.roles === "all" || item.roles.includes(role);
}

export function Layout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [resetting, setResetting] = useState(false);
  const meta = useQuery({ queryKey: ["meta"], queryFn: () => api<Meta>("/api/meta"), staleTime: 60_000 });

  if (!user) return null;

  const reset = async () => {
    if (!confirm("Reset all demo data to the starting state?")) return;
    setResetting(true);
    try {
      await post("/api/demo/reset");
      await queryClient.invalidateQueries();
      navigate("/");
    } finally {
      setResetting(false);
    }
  };

  return (
    <div className="flex min-h-full">
      <aside className="hidden w-56 shrink-0 flex-col border-r border-slate-200 bg-white md:flex">
        <div className="flex items-center gap-2 px-4 py-4">
          <div className="grid size-8 place-items-center rounded-lg bg-brand-600 text-white">
            <ScanLine className="size-4" />
          </div>
          <div className="leading-tight">
            <p className="text-sm font-semibold text-slate-900">Imaging Ops</p>
            <p className="text-xs text-slate-500">Copilot · demo</p>
          </div>
        </div>
        <nav className="flex-1 space-y-0.5 px-2" aria-label="Main">
          {NAV.filter((item) => canSee(item, user.role)).map((item, i, items) => (
            <div key={item.to}>
            {item.section && item.section !== items[i - 1]?.section && (
              <p className="px-3 pt-4 pb-1 text-[11px] font-semibold tracking-wide text-slate-400 uppercase">{item.section}</p>
            )}
            <NavLink
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                clsx(
                  "flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium",
                  isActive ? "bg-brand-50 text-brand-700" : "text-slate-600 hover:bg-slate-50 hover:text-slate-900",
                )
              }
            >
              <item.icon className="size-4" />
              {item.label}
            </NavLink>
            </div>
          ))}
        </nav>
        <div className="border-t border-slate-100 p-3 text-xs text-slate-500">
          Synthetic data only. AI output is a draft for staff review.
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 bg-white px-4 py-2.5 md:px-6">
          <nav className="flex gap-1 overflow-x-auto md:hidden" aria-label="Main mobile">
            {NAV.filter((item) => canSee(item, user.role)).map((item) => (
              <NavLink key={item.to} to={item.to} end={item.to === "/"} className={({ isActive }) => clsx("rounded-md p-2", isActive ? "bg-brand-50 text-brand-700" : "text-slate-500")} aria-label={item.label}>
                <item.icon className="size-4" />
              </NavLink>
            ))}
          </nav>
          <div className="hidden md:block">
            {meta.data && (
              <Badge tone={meta.data.llm_mode === "anthropic" ? "ai" : "slate"}>
                <Sparkles className="size-3" />
                {meta.data.llm_mode === "anthropic" ? "AI: Claude API" : "AI: mock mode (no API key)"}
              </Badge>
            )}
          </div>
          <div className="flex items-center gap-2">
            <div className="text-right leading-tight">
              <p className="text-sm font-medium text-slate-900" data-testid="current-user">{user.name}</p>
              <p className="text-xs text-slate-500">{ROLE_LABEL[user.role]}{user.site_ids.length ? ` · ${user.site_ids.join(", ")}` : ""}</p>
            </div>
            <Button size="sm" variant="ghost" onClick={reset} loading={resetting} title="Reset demo data">
              <RotateCcw className="size-4" />
              <span className="hidden lg:inline">Reset demo</span>
            </Button>
            <Button size="sm" variant="secondary" onClick={() => { logout(); navigate("/login"); }}>
              <LogOut className="size-4" />
              Switch role
            </Button>
          </div>
        </header>
        <main className="flex-1 px-4 py-5 md:px-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
