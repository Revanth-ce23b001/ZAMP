import { Suspense } from "react";

import WorkQueue from "@/components/WorkQueue";

export default function WorkQueuePage() {
  return (
    <Suspense>
      <WorkQueue />
    </Suspense>
  );
}
