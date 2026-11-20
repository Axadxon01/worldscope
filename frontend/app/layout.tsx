import type { Metadata, Viewport } from "next";
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
        {children}
      </body>
    </html>
  );
}
