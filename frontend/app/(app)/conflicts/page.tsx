import type { Metadata } from "next";
import { SectionPage } from "@/components/section-page";

export const metadata: Metadata = { title: "Conflicts & Security" };

export default function ConflictsPage() {
  return <SectionPage href="/conflicts" />;
}
