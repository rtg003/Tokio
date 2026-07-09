import { siweConfigured } from "@/lib/auth";
import { LoginForm } from "@/components/login/LoginForm";

// Server component: lê `siweConfigured()` (process.env, server-side) e repassa
// ao formulário client. Com a allowlist vazia (default), a tela mostra só senha.
export default function LoginPage() {
  return <LoginForm siweEnabled={siweConfigured()} />;
}
