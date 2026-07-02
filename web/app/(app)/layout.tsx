import { redirect } from "next/navigation";
import Shell from "@/components/Shell";
import { createClient, supabaseConfigured } from "@/lib/supabase/server";

export default async function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  if (!supabaseConfigured()) {
    redirect("/login");
  }
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) {
    redirect("/login");
  }
  return <Shell email={user.email ?? ""}>{children}</Shell>;
}
