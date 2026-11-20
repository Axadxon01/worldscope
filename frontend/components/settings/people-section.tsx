import { revokeInvitation, setUserDisabled } from "@/app/actions/admin";
import { InviteForm } from "@/components/settings/invite-form";
import { RowAction } from "@/components/settings/row-action";
import { api } from "@/lib/server/api";
import { requireToken } from "@/lib/server/auth";
import { formatDate } from "@/lib/format";
import type { Invitation, User } from "@/lib/types";

const ROLE = { admin: "Administrator", analyst: "Analyst" } as const;
const STATUS = {
  pending: "Waiting",
  used: "Accepted",
  revoked: "Withdrawn",
  expired: "Expired",
} as const;

/** Administrators only: who can sign in, and invitations to join. */
export async function PeopleSection({ currentUserId }: { currentUserId: string }) {
  const token = await requireToken();
  const [users, invitations] = await Promise.all([
    api<User[]>("/api/v1/admin/users", { token }),
    api<Invitation[]>("/api/v1/admin/invitations", { token }),
  ]);
  if (!users.ok || !invitations.ok) {
    return (
      <p className="mt-4 text-muted-foreground">
        {(!users.ok && users.detail) || (!invitations.ok && invitations.detail)}
      </p>
    );
  }
  const open = invitations.data.filter((inv) => inv.status === "pending");
  const past = invitations.data.filter((inv) => inv.status !== "pending").slice(0, 10);

  return (
    <div className="mt-4 space-y-8">
      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full text-left text-ui">
          <caption className="sr-only">People who can sign in</caption>
          <thead className="border-b bg-muted/40 text-meta text-muted-foreground">
            <tr>
              <th scope="col" className="px-4 py-2 font-medium">
                Name
              </th>
              <th scope="col" className="hidden px-4 py-2 font-medium sm:table-cell">
                Role
              </th>
              <th scope="col" className="hidden px-4 py-2 font-medium md:table-cell">
                Last sign-in
              </th>
              <th scope="col" className="px-4 py-2 text-right font-medium">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {users.data.map((user) => (
              <tr key={user.id} className={user.disabled ? "text-muted-foreground" : undefined}>
                <td className="px-4 py-3">
                  <span className="block text-foreground">
                    {user.display_name}
                    {user.id === currentUserId && (
                      <span className="text-muted-foreground"> (you)</span>
                    )}
                  </span>
                  <span className="block text-meta text-muted-foreground">
                    {user.email}
                    {user.disabled && ", disabled"}
                  </span>
                </td>
                <td className="hidden px-4 py-3 sm:table-cell">{ROLE[user.role]}</td>
                <td className="hidden px-4 py-3 md:table-cell">{formatDate(user.last_login_at)}</td>
                <td className="px-4 py-3">
                  {user.id !== currentUserId && (
                    <RowAction
                      action={setUserDisabled}
                      fields={{
                        id: user.id,
                        disabled: user.disabled ? "false" : "true",
                      }}
                      label={user.disabled ? "Enable" : "Disable"}
                      pendingLabel={user.disabled ? "Enabling…" : "Disabling…"}
                    />
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div>
        <h3 className="text-ui font-semibold text-foreground">Invite someone</h3>
        <p className="mt-1 mb-4 text-muted-foreground">
          WorldScope sends no email yet: you get a link to pass on yourself.
        </p>
        <InviteForm />
      </div>

      {(open.length > 0 || past.length > 0) && (
        <div>
          <h3 className="text-ui font-semibold text-foreground">Invitations</h3>
          <ul className="mt-3 divide-y rounded-lg border">
            {[...open, ...past].map((inv) => (
              <li key={inv.id} className="flex items-start justify-between gap-4 px-4 py-3">
                <div className="min-w-0">
                  <p className="truncate text-foreground">{inv.email}</p>
                  <p className="text-meta text-muted-foreground">
                    {STATUS[inv.status]}, {ROLE[inv.role].toLowerCase()}, sent{" "}
                    {formatDate(inv.created_at)}
                    {inv.invited_by ? ` by ${inv.invited_by}` : ""}
                    {inv.status === "pending"
                      ? `; link valid until ${formatDate(inv.expires_at)}`
                      : ""}
                  </p>
                </div>
                {inv.status === "pending" && (
                  <RowAction
                    action={revokeInvitation}
                    fields={{ id: inv.id }}
                    label="Withdraw"
                    pendingLabel="Withdrawing…"
                  />
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
