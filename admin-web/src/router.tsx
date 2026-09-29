import { EmptyState, buttonVariants } from "@omnisend/web-shared";
import { createBrowserRouter, Link } from "react-router";

import { Layout } from "./Layout";
import { AuditLogsPage } from "./pages/AuditLogs";
import { CampaignDetailPage } from "./pages/CampaignDetail";
import { CampaignsPage } from "./pages/Campaigns";
import { DashboardPage } from "./pages/Dashboard";
import { ProviderDetailPage } from "./pages/ProviderDetail";
import { ProvidersPage } from "./pages/Providers";
import { QueuesPage } from "./pages/Queues";
import { ReportsPage } from "./pages/Reports";
import { SettingsPage } from "./pages/Settings";
import { SuppressionsPage } from "./pages/Suppressions";
import { UsersPage } from "./pages/Users";
import { WorkersPage } from "./pages/Workers";

function NotFound() {
  return (
    <EmptyState
      title="Page not found"
      description="The page you are looking for does not exist."
      action={
        <Link to="/" className={buttonVariants({ variant: "secondary" })}>
          Back to dashboard
        </Link>
      }
    />
  );
}

export const router = createBrowserRouter([
  {
    path: "/",
    element: <Layout />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: "users", element: <UsersPage /> },
      { path: "campaigns", element: <CampaignsPage /> },
      { path: "campaigns/:id", element: <CampaignDetailPage /> },
      { path: "providers", element: <ProvidersPage /> },
      { path: "providers/:id", element: <ProviderDetailPage /> },
      { path: "workers", element: <WorkersPage /> },
      { path: "queues", element: <QueuesPage /> },
      { path: "reports", element: <ReportsPage /> },
      { path: "suppressions", element: <SuppressionsPage /> },
      { path: "audit-logs", element: <AuditLogsPage /> },
      { path: "settings", element: <SettingsPage /> },
      { path: "*", element: <NotFound /> },
    ],
  },
]);
