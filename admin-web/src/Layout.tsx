import { Button, LoginScreen, PageLoader, ThemeToggle, cn, useAuth } from "@omnisend/web-shared";
import {
  BarChart3,
  Cpu,
  FileClock,
  Layers,
  LayoutDashboard,
  LogOut,
  Mail,
  Menu,
  Server,
  Settings,
  ShieldBan,
  Users,
  X,
} from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router";

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, perm: "reports.read", end: true },
  { to: "/users", label: "Users", icon: Users, perm: "users.read" },
  { to: "/campaigns", label: "Campaigns", icon: Mail, perm: "campaigns.read" },
  { to: "/providers", label: "Providers", icon: Server, perm: "providers.read" },
  { to: "/workers", label: "Workers", icon: Cpu, perm: "workers.read" },
  { to: "/queues", label: "Queues", icon: Layers, perm: "queues.read" },
  { to: "/reports", label: "Reports", icon: BarChart3, perm: "reports.read" },
  { to: "/suppressions", label: "Suppressions", icon: ShieldBan, perm: "suppressions.read" },
  { to: "/audit-logs", label: "Audit logs", icon: FileClock, perm: "audit.read" },
  { to: "/settings", label: "Settings", icon: Settings, perm: "settings.read" },
];

function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const { can } = useAuth();
  return (
    <nav aria-label="Main" className="flex flex-col gap-0.5 p-3">
      {NAV.filter((n) => can(n.perm)).map(({ to, label, icon: Icon, end }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          onClick={onNavigate}
          className={({ isActive }) =>
            cn(
              "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors [&_svg]:size-4",
              isActive ? "bg-primary-soft text-primary" : "text-fg-secondary hover:bg-surface-2 hover:text-fg",
            )
          }
        >
          <Icon aria-hidden />
          {label}
        </NavLink>
      ))}
    </nav>
  );
}

function Brand() {
  return (
    <div className="flex items-center gap-2.5 px-5 py-4">
      <div className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-fg">
        <Mail className="size-4" />
      </div>
      <div className="leading-tight">
        <div className="text-sm font-semibold">OmniSendPro</div>
        <div className="text-xs text-fg-muted">Admin console</div>
      </div>
    </div>
  );
}

export function Layout() {
  const { status, user, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const location = useLocation();

  if (status === "loading") return <PageLoader />;
  if (status !== "authenticated" || !user)
    return <LoginScreen product="OmniSendPro Admin" subtitle="Sign in to the control plane" footer="Administrator access requires two-factor authentication." />;

  return (
    <div className="flex min-h-full">
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r border-border bg-surface lg:flex">
        <Brand />
        <div className="flex-1 overflow-y-auto">
          <Sidebar />
        </div>
      </aside>

      {open && (
        <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
          <div className="absolute inset-0 bg-black/40" onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 left-0 flex w-64 flex-col bg-surface shadow-pop">
            <div className="flex items-center justify-between pr-3">
              <Brand />
              <Button variant="ghost" size="icon-sm" aria-label="Close navigation" onClick={() => setOpen(false)}>
                <X />
              </Button>
            </div>
            <Sidebar onNavigate={() => setOpen(false)} />
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-border bg-surface/90 px-4 backdrop-blur lg:px-8">
          <Button variant="ghost" size="icon-sm" className="lg:hidden" aria-label="Open navigation" onClick={() => setOpen(true)}>
            <Menu />
          </Button>
          <div className="flex-1" />
          <ThemeToggle />
          <div className="hidden text-right leading-tight sm:block">
            <div className="text-sm font-medium">{user.full_name || user.username}</div>
            <div className="text-xs text-fg-muted">{user.role.replace("_", " ").toLowerCase()}</div>
          </div>
          <Button variant="ghost" size="icon-sm" aria-label="Sign out" title="Sign out" onClick={logout}>
            <LogOut />
          </Button>
        </header>
        <main key={location.pathname} className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 lg:px-8 lg:py-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
