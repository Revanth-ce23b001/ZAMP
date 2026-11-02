export default function Placeholder({ title }: { title: string }) {
  return (
    <section>
      <h1 className="text-2xl font-semibold">{title}</h1>
      <p className="mt-2 text-gray-500">Built on a later day.</p>
    </section>
  );
}
