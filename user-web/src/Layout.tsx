import { Button, LoginScreen, PageLoader, ThemeToggle, cn, useAuth } from "@omnisend/web-shared";
import { BarChart3, LayoutDashboard, LogOut, Mail, UserRound } from "lucide-react";
import { NavLink, Outlet, useLocation } from "react-router";

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/campaigns", label: "Campaigns", icon: Mail },
  { to: "/reports", label: "Reports", icon: BarChart3 },
  { to: "/profile", label: "Profile", icon: UserRound },
];

export function Layout() {
  const { status, user, logout } = useAuth();
  const location = useLocation();
  if (status === "loading") return <PageLoader />;
  if (status !== "authenticated" || !user) return <LoginScreen product="OmniSendPro" subtitle="Sign in to send your campaigns" />;
  return (
    <div className="flex min-h-full flex-col">
      <header className="sticky top-0 z-30 border-b border-border bg-surface/90 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-4 px-4">
          <div className="flex items-center gap-2">
            <div className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-fg">
              <Mail className="size-4" />
            </div>
            <span className="hidden text-sm font-semibold sm:inline">OmniSendPro</span>
          </div>
          <nav aria-label="Main" className="flex flex-1 gap-1 overflow-x-auto">
            {NAV.map(({ to, label, icon: Icon, end }) => (
              <NavLink
                key={to}
                to={to}
                end={end}
                aria-label={label}
                className={({ isActive }) =>
                  cn(
                    "flex items-center gap-2 whitespace-nowrap rounded-lg px-3 py-1.5 text-sm font-medium [&_svg]:size-4",
                    isActive ? "bg-primary-soft text-primary" : "text-fg-secondary hover:bg-surface-2 hover:text-fg",
                  )
                }
              >
                <Icon aria-hidden />
                <span className="hidden sm:inline">{label}</span>
              </NavLink>
            ))}
          </nav>
          <ThemeToggle />
          <span className="hidden text-sm text-fg-secondary md:inline">{user.full_name || user.username}</span>
          <Button variant="ghost" size="icon-sm" aria-label="Sign out" title="Sign out" onClick={logout}>
            <LogOut />
          </Button>
        </div>
      </header>
      <main key={location.pathname} className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 lg:py-8">
        <Outlet />
      </main>
    </div>
  );
}
