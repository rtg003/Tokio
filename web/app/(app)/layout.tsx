import { redirect } from "next/navigation";
import { cookies } from "next/headers";
import Shell from "@/components/Shell";
import { authConfigured, SESSION_COOKIE, verifySession } from "@/lib/auth";
import { getWallets } from "@/lib/copy-trade/data";
import { readEnv, readWallet } from "@/lib/prefs";

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
  // Controle GLOBAL (wallet + ambiente) vive no topo (Shell), lido dos cookies.
  const env = readEnv(cookieStore);
  const wallet = readWallet(cookieStore);
  const wallets = await getWallets();
  return (
    <Shell email="operador" env={env} wallet={wallet} wallets={wallets}>
      {children}
    </Shell>
  );
}
