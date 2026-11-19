import type { Metadata } from "next";
import { SectionPage } from "@/components/section-page";

export const metadata: Metadata = { title: "Sources" };

export default function SourcesPage() {
  return <SectionPage href="/sources" />;
}
