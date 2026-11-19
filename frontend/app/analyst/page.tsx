import type { Metadata } from "next";
import { SectionPage } from "@/components/section-page";

export const metadata: Metadata = { title: "AI Analyst" };

export default function AnalystPage() {
  return <SectionPage href="/analyst" />;
}
