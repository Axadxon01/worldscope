import type { Metadata } from "next";
import { Suspense } from "react";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { PasswordForm } from "@/components/settings/password-form";
import { PeopleSection } from "@/components/settings/people-section";
import { StatusPanel } from "@/components/status-panel";
import { getMe } from "@/lib/server/auth";
import { formatDateTime } from "@/lib/format";
import { sectionFor } from "@/lib/sections";

export const metadata: Metadata = { title: "Settings" };

const ROLE = { admin: "Administrator", analyst: "Analyst" } as const;

function Heading({ id, children }: { id: string; children: React.ReactNode }) {
  return (
    <h2 id={id} className="font-serif text-[1.3125rem] font-semibold">
      {children}
    </h2>
  );
}

async function Account() {
  const { user, session_expires_at } = await getMe();
  return (
    <>
      <section aria-labelledby="account-heading" className="mt-8 max-w-[44rem]">
        <Heading id="account-heading">Your account</Heading>
        <dl className="mt-4 grid grid-cols-[8rem_1fr] gap-x-4 gap-y-2 rounded-lg border bg-card px-5 py-4">
          <dt className="text-muted-foreground">Name</dt>
          <dd>{user.display_name}</dd>
          <dt className="text-muted-foreground">Email</dt>
          <dd className="break-all">{user.email}</dd>
          <dt className="text-muted-foreground">Role</dt>
          <dd>{ROLE[user.role]}</dd>
          <dt className="text-muted-foreground">Signed in until</dt>
          <dd>{formatDateTime(session_expires_at)}, renewed while you use WorldScope</dd>
        </dl>
        <h3 className="mt-7 mb-3 text-ui font-semibold text-foreground">Change password</h3>
        <PasswordForm />
      </section>

      {user.role === "admin" && (
        <section aria-labelledby="people-heading" className="mt-12 max-w-[56rem]">
          <Heading id="people-heading">People</Heading>
          <p className="mt-1.5 max-w-[44rem] text-muted-foreground">
            Everyone who can sign in. Disabling an account signs it out everywhere at once.
          </p>
          <Suspense fallback={<p className="mt-4 text-muted-foreground">Loading people…</p>}>
            <PeopleSection currentUserId={user.id} />
          </Suspense>
        </section>
      )}
    </>
  );
}

export default function SettingsPage() {
  const section = sectionFor("/settings");
  return (
    <>
      <PageHeader section={section} />

      <Suspense fallback={<p className="mt-8 text-muted-foreground">Loading your account…</p>}>
        <Account />
      </Suspense>

      <section aria-labelledby="platform-heading" className="mt-12 max-w-[44rem]">
        <Heading id="platform-heading">Platform status</Heading>
        <p className="mt-1.5 text-muted-foreground">
          Live checks of each service. A worker heartbeat arrives every minute once the scheduler
          and a worker are running.
        </p>
        <div className="mt-4 rounded-lg border bg-card px-5 py-4">
          <StatusPanel detailed />
        </div>
      </section>

      <EmptyState section={section} title="Alerts" />
    </>
  );
}
