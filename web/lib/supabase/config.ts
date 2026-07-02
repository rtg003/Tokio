// Shared guard: a malformed NEXT_PUBLIC_SUPABASE_URL (missing scheme, quotes,
// stray spaces) must degrade to the "unconfigured" flow (login screen with a
// notice) — never a 500 on every route.
export function supabaseConfigured(): boolean {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
  const anon = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "";
  if (!anon.trim()) return false;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
}
