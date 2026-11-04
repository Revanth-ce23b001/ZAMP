"use client";

// The thinnest possible way to start a run (Day 3B): pick a sample or drop a PDF. Both go through
// the backend's single upload path; an already-processed file opens its existing run (FR-004).
// It sits at the top of the Work Queue (FR-002).

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
      // FR-004 / §10.3: an already-processed file opens its decision screen with the banner.
      router.push(r.existing ? `/runs/${r.run_id}?existing=1` : `/runs/${r.run_id}/live`);
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
    <div className="mt-5 flex flex-col gap-4 md:flex-row">
      <div className="card flex-1">
        <label htmlFor="sample" className="card-title block">Run sample</label>
        <div className="mt-2 flex gap-2">
          <select id="sample" value={selected} onChange={(e) => setSelected(e.target.value)}
                  className="input flex-1">
            {samples.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
          </select>
          <button type="button" disabled={!selected || busy} onClick={() => go(() => processSample(selected))}
                  className="btn btn-primary">
            Run
          </button>
        </div>
      </div>

      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); onFiles(e.dataTransfer.files); }}
        onClick={() => input.current?.click()}
        className={"flex flex-1 cursor-pointer flex-col items-center justify-center gap-1 rounded-card border border-dashed p-6 text-sm " +
          (dragging ? "border-ink bg-hover" : "border-ink-4 bg-surface hover:bg-sunken")}
      >
        <span className="card-title">Drag and drop an invoice PDF here</span>
        <span className="text-ink-3">or click to browse (PDF, up to 10 MB, up to 5 pages)</span>
        <input ref={input} type="file" accept="application/pdf" className="hidden" data-testid="upload-input"
               onChange={(e) => onFiles(e.target.files)} />
      </div>

      {message && <div role="alert" className="text-sm text-bad md:basis-full">{message}</div>}
    </div>
  );
}
