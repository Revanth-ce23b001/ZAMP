"use client";

import { useParams, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import DecisionView from "@/components/decision/DecisionView";

function Decision() {
  const { id } = useParams<{ id: string }>();
  const existing = useSearchParams().get("existing") === "1";
  return <DecisionView runId={id} existing={existing} />;
}

export default function DecisionPage() {
  return (
    <Suspense>
      <Decision />
    </Suspense>
  );
}
