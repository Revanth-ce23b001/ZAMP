"use client";

import { useParams, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import LiveRun from "@/components/LiveRun";

function Live() {
  const { id } = useParams<{ id: string }>();
  const existing = useSearchParams().get("existing") === "1";
  return <LiveRun runId={id} existing={existing} />;
}

export default function LiveRunPage() {
  return (
    <Suspense>
      <Live />
    </Suspense>
  );
}
