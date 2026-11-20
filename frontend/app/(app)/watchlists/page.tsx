import type { Metadata } from "next";
import { SectionPage } from "@/components/section-page";

export const metadata: Metadata = { title: "Watchlists" };

export default function WatchlistsPage() {
  return <SectionPage href="/watchlists" />;
}
