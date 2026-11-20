import type { Metadata } from "next";
import { SectionPage } from "@/components/section-page";

export const metadata: Metadata = { title: "Global News" };

export default function NewsPage() {
  return <SectionPage href="/news" />;
}
