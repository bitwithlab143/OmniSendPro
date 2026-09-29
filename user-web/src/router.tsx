import { EmptyState, buttonVariants } from "@omnisend/web-shared";
import { createBrowserRouter, Link } from "react-router";

import { Layout } from "./Layout";
import { CampaignEditorPage } from "./pages/CampaignEditor";
import { CampaignMonitorPage } from "./pages/CampaignMonitor";
import { CampaignsPage } from "./pages/Campaigns";
import { DashboardPage } from "./pages/Dashboard";
import { ProfilePage } from "./pages/Profile";
import { ReportsPage } from "./pages/Reports";

function NotFound() {
  return (
    <EmptyState
      title="Page not found"
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
      { path: "campaigns", element: <CampaignsPage /> },
      { path: "campaigns/new", element: <CampaignEditorPage /> },
      { path: "campaigns/:id/edit", element: <CampaignEditorPage /> },
      { path: "campaigns/:id", element: <CampaignMonitorPage /> },
      { path: "reports", element: <ReportsPage /> },
      { path: "profile", element: <ProfilePage /> },
      { path: "*", element: <NotFound /> },
    ],
  },
]);
