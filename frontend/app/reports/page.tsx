import type { Metadata } from "next";
import { SectionPage } from "@/components/section-page";

export const metadata: Metadata = { title: "Reports" };

export default function ReportsPage() {
  return <SectionPage href="/reports" />;
}
