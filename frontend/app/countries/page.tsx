import type { Metadata } from "next";
import { SectionPage } from "@/components/section-page";

export const metadata: Metadata = { title: "Countries" };

export default function CountriesPage() {
  return <SectionPage href="/countries" />;
}
