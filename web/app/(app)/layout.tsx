import { redirect } from "next/navigation";
import { cookies } from "next/headers";
import Shell from "@/components/Shell";
import { authConfigured, SESSION_COOKIE, verifySession } from "@/lib/auth";

export default async function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  if (!authConfigured()) {
    redirect("/login");
  }
  const cookieStore = await cookies();
  const ok = await verifySession(cookieStore.get(SESSION_COOKIE)?.value);
  if (!ok) {
    redirect("/login");
  }
  return <Shell email="operador">{children}</Shell>;
}
