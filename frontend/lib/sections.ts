/**
 * The eleven sections of WorldScope, in the order of the product brief.
 * Each entry says what the page is for and what fills it, so empty pages can say
 * plainly what will appear and when, instead of showing placeholder data.
 */

export type SectionGroup = "Monitor" | "Investigate" | "Produce" | "System";

export interface Section {
  href: string;
  label: string;
  group: SectionGroup;
  /** One sentence, from the reader's point of view. */
  summary: string;
  /** What the page shows once its data exists. */
  willShow: string[];
  /** Plain-language reason it is empty now. */
  emptyBecause: string;
  milestone: number;
}

export const SECTIONS: Section[] = [
  {
    href: "/",
    label: "Overview",
    group: "Monitor",
    summary: "The most significant events right now, how much is being reported, and where.",
    willShow: [],
    emptyBecause: "No articles have been collected yet.",
    milestone: 6,
  },
  {
    href: "/news",
    label: "Global News",
    group: "Monitor",
    summary:
      "Every collected article, searchable and grouped into events, each linking to its original source.",
    willShow: [
      "Search across headlines and summaries",
      "Filters for country, category, date range, source and language",
      "Articles grouped into the events they describe",
      "Bookmarks you can return to",
    ],
    emptyBecause: "Collection from GDELT, RSS feeds and ReliefWeb starts in Milestone 3.",
    milestone: 4,
  },
  {
    href: "/map",
    label: "Intelligence Map",
    group: "Monitor",
    summary:
      "Events placed where their sources say they happened, at country level unless a source gives a precise location.",
    willShow: [
      "Countries shaded by the number of events",
      "Markers only where a source supplies coordinates or a named city",
      "The precision of each location, shown in its popup",
      "Filters for time, category and region",
    ],
    emptyBecause: "Locations are extracted from articles in Milestone 5.",
    milestone: 7,
  },
  {
    href: "/countries",
    label: "Countries",
    group: "Investigate",
    summary:
      "A profile for each country: leaders, recent events, timeline and related countries, each fact with its source.",
    willShow: [
      "Current leaders, from Wikidata, with the date checked",
      "Latest events and a timeline",
      "Key people and organizations in the reporting",
      "An overview written from cited articles, marked as analysis",
    ],
    emptyBecause: "Profiles are built from processed articles in Milestone 6.",
    milestone: 6,
  },
  {
    href: "/conflicts",
    label: "Conflicts & Security",
    group: "Investigate",
    summary:
      "Armed conflicts, incidents, sanctions and negotiations, each report labelled by how well it is corroborated.",
    willShow: [
      "Parties involved and the status of each situation",
      "Reports compared across sources",
      "Labels: confirmed by cited source, multiple-source, single-source, disputed, unverified",
      "Historical context and related events",
    ],
    emptyBecause: "This page is built on event clustering and timelines in Milestone 9.",
    milestone: 9,
  },
  {
    href: "/timeline",
    label: "Event Timeline",
    group: "Investigate",
    summary:
      "How events unfolded, and which earlier events may be connected, with the evidence for each possible link.",
    willShow: [
      "Events in order, with what changed at each step",
      "Possible connections between events, never presented as causes",
      "The articles that support each connection",
    ],
    emptyBecause: "The event-relationship engine arrives in Milestone 9.",
    milestone: 9,
  },
  {
    href: "/analyst",
    label: "AI Analyst",
    group: "Investigate",
    summary:
      "Ask about the collected reporting. Every answer cites its sources, separates fact from analysis, and says when evidence is thin.",
    willShow: [
      "Answers split into reported facts, analysis and speculation",
      "Clickable citations with publication times",
      "A stated confidence and its limits",
    ],
    emptyBecause: "The analyst needs indexed articles; it arrives in Milestone 8.",
    milestone: 8,
  },
  {
    href: "/reports",
    label: "Reports",
    group: "Produce",
    summary:
      "Daily briefs and country, regional, conflict and economic reports, with sources and generation time, exportable to PDF.",
    willShow: [
      "Ten-part reports, from executive summary to sources",
      "Reporting and analytical judgments kept visibly separate",
      "PDF export",
    ],
    emptyBecause: "Report generation arrives in Milestone 10.",
    milestone: 10,
  },
  {
    href: "/watchlists",
    label: "Watchlists",
    group: "Produce",
    summary:
      "The countries, topics, people and keywords you follow, with alerts when new reporting matches.",
    willShow: [
      "Watchlists built from keywords, countries, topics and entities",
      "In-app and email alerts, grouped so you are not flooded",
    ],
    emptyBecause: "Watchlists and alerts arrive in Milestone 10, after sign-in exists.",
    milestone: 10,
  },
  {
    href: "/sources",
    label: "Sources",
    group: "System",
    summary:
      "Where the data comes from: each source's terms, what we store from it, and whether its last fetch worked.",
    willShow: [
      "Every source with its type, country, language and terms of use",
      "What is stored from each: link only, or headline and summary",
      "Last successful fetch and current errors",
    ],
    emptyBecause: "The reviewed source list is loaded into the database next.",
    milestone: 2,
  },
  {
    href: "/settings",
    label: "Settings",
    group: "System",
    summary: "How the platform is running now, and later your account and alert preferences.",
    willShow: ["Your account, password and sessions", "Alert delivery preferences"],
    emptyBecause: "Sign-in and accounts are built next.",
    milestone: 2,
  },
];

export const GROUPS: SectionGroup[] = ["Monitor", "Investigate", "Produce", "System"];

export function sectionFor(href: string): Section {
  const found = SECTIONS.find((s) => s.href === href);
  if (!found) throw new Error(`Unknown section ${href}`);
  return found;
}
