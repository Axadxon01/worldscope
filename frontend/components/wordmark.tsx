import Link from "next/link";

export function Wordmark() {
  return (
    <Link href="/" className="group inline-flex flex-col rounded-sm leading-none">
      <span className="font-serif text-[1.3125rem] font-semibold tracking-[-0.01em] text-foreground">
        WorldScope
      </span>
      <span className="mt-1 text-meta text-muted-foreground group-hover:text-foreground">
        Intelligence
      </span>
    </Link>
  );
}
