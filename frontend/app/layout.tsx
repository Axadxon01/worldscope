import type { Metadata, Viewport } from "next";
import { MobileNav } from "@/components/mobile-nav";
import { SiteNav } from "@/components/site-nav";
import { StatusPanel } from "@/components/status-panel";
import { Wordmark } from "@/components/wordmark";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "WorldScope Intelligence",
    template: "%s – WorldScope",
  },
  description:
    "Global news and geopolitical intelligence: what happened, where, who is involved, and how well it is sourced.",
};

export const viewport: Viewport = {
  themeColor: "#0e1726",
  colorScheme: "dark",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="dark h-full">
      <body className="min-h-full">
        <a
          href="#content"
          className="sr-only z-50 rounded-md bg-primary px-3 py-2 text-primary-foreground focus:not-sr-only focus:fixed focus:top-3 focus:left-3"
        >
          Skip to content
        </a>

        <MobileNav />

        <div className="lg:grid lg:min-h-screen lg:grid-cols-[15.5rem_1fr]">
          <aside className="hidden border-r bg-sidebar lg:sticky lg:top-0 lg:flex lg:h-screen lg:flex-col">
            <div className="px-5 pt-6 pb-7">
              <Wordmark />
            </div>
            <div className="flex-1 overflow-y-auto px-2">
              <SiteNav />
            </div>
            <StatusPanel className="border-t px-5 py-4" />
          </aside>

          <main id="content" className="min-w-0 px-5 py-8 sm:px-8 lg:px-12 lg:py-12">
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}
