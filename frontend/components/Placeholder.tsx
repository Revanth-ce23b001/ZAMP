export default function Placeholder({ title }: { title: string }) {
  return (
    <section>
      <h1 className="page-title">{title}</h1>
      <p className="mt-2 text-ink-3">Built on a later day.</p>
    </section>
  );
}
