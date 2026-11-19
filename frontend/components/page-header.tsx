import type { Section } from "@/lib/sections";

export function PageHeader({ section }: { section: Section }) {
  return (
    <header className="max-w-[44rem] border-b pb-7">
      <h1 className="font-serif text-title font-semibold tracking-[-0.015em] text-foreground">
        {section.label}
      </h1>
      <p className="mt-3 font-serif text-lede text-muted-foreground">{section.summary}</p>
    </header>
  );
}
