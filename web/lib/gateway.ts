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
    if (!response.ok) return null;
    return (await response.json()) as T;
  } catch {
    return null;
  }
}
