import type { Metadata } from "next";
import { InviteForm } from "@/app/(auth)/invite/invite-form";

export const metadata: Metadata = { title: "Accept invitation" };

export default function InvitePage() {
  return (
    <>
      <h1 className="font-serif text-[1.75rem] leading-tight font-semibold">Join WorldScope</h1>
      <InviteForm />
    </>
  );
}
