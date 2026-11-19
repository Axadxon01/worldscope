"use client";

import {
  CalendarRange,
  Eye,
  FileText,
  Flag,
  Gauge,
  Map,
  MessagesSquare,
  Newspaper,
  Rss,
  Settings,
  ShieldAlert,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { GROUPS, SECTIONS } from "@/lib/sections";

const ICONS: Record<string, LucideIcon> = {
  "/": Gauge,
  "/news": Newspaper,
  "/map": Map,
  "/countries": Flag,
  "/conflicts": ShieldAlert,
  "/timeline": CalendarRange,
  "/analyst": MessagesSquare,
  "/reports": FileText,
  "/watchlists": Eye,
  "/sources": Rss,
  "/settings": Settings,
};

function isActive(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}

export function SiteNav({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();

  return (
    <nav aria-label="Main" className="space-y-5">
      {GROUPS.map((group) => (
        <div key={group}>
          <h2 className="px-3 pb-1.5 text-meta text-muted-foreground/80">{group}</h2>
          <ul className="space-y-px">
            {SECTIONS.filter((s) => s.group === group).map((section) => {
              const Icon = ICONS[section.href];
              const active = isActive(pathname, section.href);
              return (
                <li key={section.href}>
                  <Link
                    href={section.href}
                    onClick={onNavigate}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "relative flex items-center gap-2.5 rounded-md px-3 py-1.5 text-ui text-muted-foreground transition-colors hover:bg-accent hover:text-foreground",
                      active && "bg-accent text-foreground",
                    )}
                  >
                    {active && (
                      <span
                        aria-hidden
                        className="absolute inset-y-1.5 left-0 w-0.5 rounded-full bg-primary"
                      />
                    )}
                    <Icon aria-hidden className="size-4 shrink-0" strokeWidth={1.75} />
                    {section.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}
