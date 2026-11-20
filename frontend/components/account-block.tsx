import { signOut } from "@/app/actions/auth";
import { getMe } from "@/lib/server/auth";

const ROLE_LABEL = { admin: "Administrator", analyst: "Analyst" } as const;

/** Who is signed in, and the way out. Reads the session, so it renders behind Suspense. */
export async function AccountBlock() {
  const { user } = await getMe();
  return (
    <div className="flex items-end justify-between gap-3">
      <div className="min-w-0">
        <p className="truncate text-ui font-medium text-foreground">{user.display_name}</p>
        <p className="text-meta text-muted-foreground">{ROLE_LABEL[user.role]}</p>
      </div>
      <form action={signOut}>
        <button
          type="submit"
          className="rounded-sm text-meta text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
        >
          Sign out
        </button>
      </form>
    </div>
  );
}

export function AccountBlockFallback() {
  return (
    <div aria-hidden className="space-y-1.5">
      <div className="h-4 w-28 rounded-sm bg-muted" />
      <div className="h-3 w-16 rounded-sm bg-muted" />
    </div>
  );
}
