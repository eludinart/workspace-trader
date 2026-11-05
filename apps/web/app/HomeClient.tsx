"use client";

import dynamic from "next/dynamic";

const Dashboard = dynamic(
  () => import("@/components/Dashboard").then((m) => m.Dashboard),
  {
    ssr: false,
    loading: () => (
      <div className="flex min-h-dvh items-center justify-center text-sm text-muted">
        Chargement du dashboard…
      </div>
    ),
  }
);

export function HomeClient() {
  return <Dashboard />;
}
