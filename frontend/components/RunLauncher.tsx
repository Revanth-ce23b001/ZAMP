"use client";

// The thinnest possible way to start a run (Day 3B): pick a sample or drop a PDF. Both go through
// the backend's single upload path; an already-processed file opens its existing run (FR-004).
// The full inbox arrives on a later day.

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { listSamples, processSample, uploadInvoice, type ApiError, type Sample, type Submitted } from "@/lib/client";

export default function RunLauncher() {
  const router = useRouter();
  const [samples, setSamples] = useState<Sample[]>([]);
  const [selected, setSelected] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    listSamples()
      .then((s) => {
        setSamples(s);
        setSelected(s[0]?.name ?? "");
      })
      .catch(() => setMessage("Could not load samples. Is the backend running?"));
  }, []);

  async function go(submit: () => Promise<Submitted>) {
    setBusy(true);
    setMessage(null);
    try {
      const r = await submit();
      const q = r.existing ? "?existing=1" : "";
      router.push(`/runs/${r.run_id}/live${q}`);
    } catch (e) {
      setMessage((e as ApiError).message ?? "Upload failed");
      setBusy(false);
    }
  }

  function onFiles(files: FileList | null) {
    const file = files?.[0];
    if (file) go(() => uploadInvoice(file));
  }

  return (
    <div className="mt-6 flex flex-col gap-4 md:flex-row">
      <div className="flex-1 rounded border border-gray-200 bg-white p-4">
        <label htmlFor="sample" className="block text-sm font-medium text-gray-700">Run sample</label>
        <div className="mt-2 flex gap-2">
          <select id="sample" value={selected} onChange={(e) => setSelected(e.target.value)}
                  className="flex-1 rounded border border-gray-300 px-2 py-1.5 text-sm">
            {samples.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
          </select>
          <button type="button" disabled={!selected || busy} onClick={() => go(() => processSample(selected))}
                  className="rounded bg-blue-600 px-3 py-1.5 text-sm font-semibold text-white disabled:bg-gray-300">
            Run
          </button>
        </div>
      </div>

      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); onFiles(e.dataTransfer.files); }}
        onClick={() => input.current?.click()}
        className={"flex flex-1 cursor-pointer flex-col items-center justify-center rounded border-2 border-dashed p-4 text-sm " +
          (dragging ? "border-blue-500 bg-blue-50" : "border-gray-300 bg-white")}
      >
        <span className="font-medium text-gray-700">Drag and drop an invoice PDF here</span>
        <span className="text-gray-500">or click to browse (PDF, up to 10 MB, up to 5 pages)</span>
        <input ref={input} type="file" accept="application/pdf" className="hidden" data-testid="upload-input"
               onChange={(e) => onFiles(e.target.files)} />
      </div>

      {message && <div role="alert" className="text-sm text-red-700 md:basis-full">{message}</div>}
    </div>
  );
}
