"use client";

import { Menu } from "lucide-react";
import { useState, type ReactNode } from "react";
import { SiteNav } from "@/components/site-nav";
import { StatusPanel } from "@/components/status-panel";
import { Wordmark } from "@/components/wordmark";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";

/** Top bar with a menu sheet, shown below the large breakpoint. */
export function MobileNav({ account }: { account: ReactNode }) {
  const [open, setOpen] = useState(false);

  return (
    <header className="sticky top-0 z-40 flex items-center justify-between border-b bg-background/95 px-4 py-3 backdrop-blur lg:hidden">
      <Wordmark />
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetTrigger className="inline-flex items-center gap-2 rounded-md border px-3 py-1.5 text-ui text-foreground hover:bg-accent">
          <Menu aria-hidden className="size-4" />
          Menu
        </SheetTrigger>
        <SheetContent side="left" className="w-72 gap-0 overflow-y-auto bg-sidebar p-0">
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <div className="px-5 pt-5 pb-6">
            <Wordmark />
          </div>
          <div className="px-2">
            <SiteNav onNavigate={() => setOpen(false)} />
          </div>
          <div className="mt-auto border-t px-5 py-4">{account}</div>
          <StatusPanel className="border-t px-5 py-4" />
        </SheetContent>
      </Sheet>
    </header>
  );
}
