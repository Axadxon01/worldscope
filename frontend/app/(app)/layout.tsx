import type { ReactNode } from "react";
import { Suspense } from "react";
import { AccountBlock, AccountBlockFallback } from "@/components/account-block";
import { MobileNav } from "@/components/mobile-nav";
import { SiteNav } from "@/components/site-nav";
import { StatusPanel } from "@/components/status-panel";
import { Wordmark } from "@/components/wordmark";

/** The signed-in shell: navigation, the account, and platform status. */
export default function AppLayout({ children }: { children: ReactNode }) {
  const account = (
    <Suspense fallback={<AccountBlockFallback />}>
      <AccountBlock />
    </Suspense>
  );
  return (
    <>
      <MobileNav account={account} />

      <div className="lg:grid lg:min-h-screen lg:grid-cols-[15.5rem_1fr]">
        <aside className="hidden border-r bg-sidebar lg:sticky lg:top-0 lg:flex lg:h-screen lg:flex-col">
          <div className="px-5 pt-6 pb-7">
            <Wordmark />
          </div>
          <div className="flex-1 overflow-y-auto px-2">
            <SiteNav />
          </div>
          <div className="border-t px-5 py-4">{account}</div>
          <StatusPanel className="border-t px-5 py-4" />
        </aside>

        <main id="content" className="min-w-0 px-5 py-8 sm:px-8 lg:px-12 lg:py-12">
          {children}
        </main>
      </div>
    </>
  );
}
