import type { Metadata } from "next";
import { SectionPage } from "@/components/section-page";

export const metadata: Metadata = { title: "Intelligence Map" };

export default function MapPage() {
  return <SectionPage href="/map" />;
}
