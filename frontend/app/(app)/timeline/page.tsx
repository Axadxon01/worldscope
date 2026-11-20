import type { Metadata } from "next";
import { SectionPage } from "@/components/section-page";

export const metadata: Metadata = { title: "Event Timeline" };

export default function TimelinePage() {
  return <SectionPage href="/timeline" />;
}
