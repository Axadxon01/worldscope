import { ExternalLink } from "lucide-react";
import type { Metadata } from "next";
import { Suspense } from "react";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { api } from "@/lib/server/api";
import { requireToken } from "@/lib/server/auth";
import { sectionFor } from "@/lib/sections";
import type { Source } from "@/lib/types";

export const metadata: Metadata = { title: "Sources" };

const TYPE = {
  rss: "RSS feed",
  official: "Official feed",
  api: "API",
} as const;
const OWNERSHIP: Record<string, string> = {
  public_broadcaster: "Public broadcaster",
  state: "State-owned",
  state_funded: "State-funded",
  government: "Government",
  intergovernmental: "Intergovernmental",
  private: "Private",
  nonprofit: "Non-profit",
};
const LANGUAGE: Record<string, string> = {
  en: "English",
  ru: "Russian",
  uz: "Uzbek",
  multi: "many languages",
};
const STORES = {
  snippet: "Headline, feed summary and link",
  link_only: "Headline and link",
} as const;

const GROUPS: {
  key: string;
  title: string;
  note: string;
  match: (s: Source) => boolean;
}[] = [
  {
    key: "collecting",
    title: "Collecting",
    note: "Terms reviewed and approved. Collection starts in Milestone 3.",
    match: (s) => s.enabled,
  },
  {
    key: "approved-off",
    title: "Approved, not switched on",
    note: "Terms allow our use, but the source is off, usually because it needs an access key.",
    match: (s) => !s.enabled && s.review_status === "approved",
  },
  {
    key: "pending",
    title: "Waiting for terms review",
    note: "Nothing is collected from these until their terms have been read and approved.",
    match: (s) => s.review_status === "pending",
  },
  {
    key: "rejected",
    title: "Not used",
    note: "Their terms do not allow the way WorldScope would use them.",
    match: (s) => s.review_status === "rejected",
  },
];

function SourceRow({ source }: { source: Source }) {
  const publisher = [
    source.publisher_country?.name,
    source.ownership ? OWNERSHIP[source.ownership] : null,
  ]
    .filter(Boolean)
    .join(", ");
  return (
    <tr className="align-top">
      <td className="px-4 py-3">
        <a
          href={source.homepage}
          target="_blank"
          rel="noopener noreferrer"
          className="text-foreground underline-offset-4 hover:underline"
        >
          {source.name}
          <ExternalLink
            aria-hidden
            className="ml-1 inline-block size-3 align-[-1px] text-muted-foreground"
          />
          <span className="sr-only">(opens the publisher&apos;s site)</span>
        </a>
        <p className="text-meta text-muted-foreground">
          {TYPE[source.type]} in {LANGUAGE[source.language] ?? source.language}
          {source.is_primary && ", primary source"}
        </p>
        <p className="mt-1 text-meta text-muted-foreground md:hidden">{publisher}</p>
        {source.review_note && (
          <p className="mt-1.5 max-w-[34rem] text-meta text-muted-foreground">
            {source.review_note}
          </p>
        )}
      </td>
      <td className="hidden px-4 py-3 md:table-cell">{publisher || "Not confirmed"}</td>
      <td className="hidden px-4 py-3 lg:table-cell">{STORES[source.storage_policy]}</td>
      <td className="px-4 py-3">
        {source.licence ? (
          <span className="block max-w-[16rem]">{source.licence}</span>
        ) : (
          <span className="text-muted-foreground">Not reviewed</span>
        )}
        {source.terms_url && (
          <a
            href={source.terms_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-meta text-primary underline-offset-4 hover:underline"
          >
            Read the terms
          </a>
        )}
      </td>
    </tr>
  );
}

async function SourceList() {
  const token = await requireToken();
  const result = await api<{ sources: Source[] }>("/api/v1/sources", { token });
  if (!result.ok) throw new Error(result.detail);
  const { sources } = result.data;

  return (
    <div className="mt-8 space-y-12">
      <p className="max-w-[44rem] text-muted-foreground">
        {sources.length} sources are configured. WorldScope stores headlines, feed summaries and
        links, never full articles, and every item links back to its publisher.
      </p>
      {GROUPS.map((group) => {
        const rows = sources.filter(group.match);
        if (rows.length === 0) return null;
        const headingId = `sources-${group.key}`;
        return (
          <section key={group.key} aria-labelledby={headingId}>
            <h2 id={headingId} className="font-serif text-[1.3125rem] font-semibold">
              {group.title} <span className="text-muted-foreground">({rows.length})</span>
            </h2>
            <p className="mt-1 max-w-[44rem] text-muted-foreground">{group.note}</p>
            <div className="mt-4 overflow-x-auto rounded-lg border">
              <table className="w-full text-left text-ui">
                <thead className="border-b bg-muted/40 text-meta text-muted-foreground">
                  <tr>
                    <th scope="col" className="px-4 py-2 font-medium">
                      Source
                    </th>
                    <th scope="col" className="hidden px-4 py-2 font-medium md:table-cell">
                      Publisher
                    </th>
                    <th scope="col" className="hidden px-4 py-2 font-medium lg:table-cell">
                      What is stored
                    </th>
                    <th scope="col" className="px-4 py-2 font-medium">
                      Licence
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {rows.map((source) => (
                    <SourceRow key={source.slug} source={source} />
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        );
      })}
    </div>
  );
}

export default function SourcesPage() {
  const section = sectionFor("/sources");
  return (
    <>
      <PageHeader section={section} />
      <Suspense fallback={<p className="mt-8 text-muted-foreground">Loading sources…</p>}>
        <SourceList />
      </Suspense>
      <EmptyState section={section} title="Fetch health" />
    </>
  );
}
