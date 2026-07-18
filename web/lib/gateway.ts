export function gatewayBase(): string {
  const host = process.env.GATEWAY_HOST ?? "127.0.0.1";
  const port = process.env.GATEWAY_PORT ?? "8700";
  return `http://${host}:${port}`;
}

export async function gatewayGet<T>(path: string): Promise<T | null> {
  try {
    const response = await fetch(`${gatewayBase()}${path}`, {
      cache: "no-store",
      signal: AbortSignal.timeout(4000),
    });
    if (!response.ok) {
      // UPDATE-0065 (Fix 4): a falha era silenciosa — um 400/500 do gateway virava
      // `null` e a tabela ficava vazia sem rastro. Loga path + status p/ diagnóstico.
      console.warn(`gatewayGet: ${path} → HTTP ${response.status}`);
      return null;
    }
    return (await response.json()) as T;
  } catch (err) {
    console.warn(`gatewayGet: ${path} → ${err instanceof Error ? err.message : String(err)}`);
    return null;
  }
}
