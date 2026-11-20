import { PageHeader } from "@/components/page-header";
import { cn } from "@/lib/utils";
import { sectionFor } from "@/lib/sections";

interface Region {
  title: string;
  shows: string;
  fills: string;
  className: string;
}

/**
 * The dashboard's layout, drawn as outlines until real data exists. Each region says
 * what will fill it; no chart is drawn without data behind it.
 */
const REGIONS: Region[] = [
  {
    title: "Latest important events",
    shows:
      "The highest-ranked events of the last 24 hours, each with its verification label, number of independent sources and countries involved.",
    fills: "Fills once articles are grouped into events (Milestone 5).",
    className: "xl:col-span-4 xl:row-span-2 min-h-56 xl:min-h-[23rem]",
  },
  {
    title: "Reporting volume",
    shows: "Articles collected per hour over the last seven days, by source family.",
    fills: "Fills once collection runs (Milestone 3).",
    className: "xl:col-span-2",
  },
  {
    title: "Emerging topics",
    shows: "Topics whose coverage grew fastest in the last 48 hours compared with the week before.",
    fills: "Fills once articles are categorized (Milestone 5).",
    className: "xl:col-span-2",
  },
  {
    title: "Events by region",
    shows: "Event counts for each world region, with Central Asia shown on its own.",
    fills: "Fills once locations are extracted (Milestone 5).",
    className: "xl:col-span-2",
  },
  {
    title: "Events by category",
    shows: "Counts across the twelve categories, from geopolitics to public health.",
    fills: "Fills once articles are categorized (Milestone 5).",
    className: "xl:col-span-2",
  },
  {
    title: "Risk indicators",
    shows: "Developments worth watching, each tied to the events and sources behind it.",
    fills: "Fills once related events are linked (Milestone 9).",
    className: "xl:col-span-2",
  },
];

export default function OverviewPage() {
  const section = sectionFor("/");
  return (
    <>
      <PageHeader section={section} />

      <p className="mt-7 max-w-[44rem] text-muted-foreground">
        Nothing has been collected yet, so this page shows where each part of the overview will
        sit and what will fill it. Every chart here will be drawn from collected data only.
      </p>

      <div className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-6">
        {REGIONS.map((region) => (
          <section
            key={region.title}
            aria-label={region.title}
            className={cn(
              "flex flex-col rounded-lg border border-dashed px-5 py-4",
              "bg-[repeating-linear-gradient(135deg,transparent_0_10px,rgb(147_161_181/0.035)_10px_11px)]",
              region.className,
            )}
          >
            <h2 className="text-ui font-semibold text-foreground">{region.title}</h2>
            <p className="mt-1.5 max-w-[36rem] text-muted-foreground">{region.shows}</p>
            <p className="mt-auto pt-6 text-meta text-muted-foreground/80">{region.fills}</p>
          </section>
        ))}
      </div>
    </>
  );
}
