import Link from "next/link";

export default function NotFound() {
  return (
    <div className="max-w-[44rem]">
      <h1 className="font-serif text-title font-semibold">Page not found</h1>
      <p className="mt-3 font-serif text-lede text-muted-foreground">
        There is no page at this address. Check the link, or go back to the overview.
      </p>
      <Link
        href="/"
        className="mt-6 inline-flex rounded-md border px-3 py-1.5 text-foreground hover:bg-accent"
      >
        Go to the overview
      </Link>
    </div>
  );
}
