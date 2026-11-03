import RunLauncher from "@/components/RunLauncher";

export default function WorkQueuePage() {
  return (
    <section>
      <h1 className="text-2xl font-semibold">Invoice Work Queue</h1>
      <p className="mt-2 text-gray-500">Start a run. The full inbox is built on a later day.</p>
      <RunLauncher />
    </section>
  );
}
