import { cookies } from "next/headers";
import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE } from "@/lib/session-cookie";

/** The API no longer accepts this browser's session: forget it and ask for sign-in. */
export async function GET(request: NextRequest): Promise<NextResponse> {
  (await cookies()).delete(SESSION_COOKIE);
  return NextResponse.redirect(new URL("/login?ended=1", request.url), 303);
}
