export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toLocaleString("pt-BR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function fmtSigned(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined) return "—";
  const s = fmtNum(Math.abs(v), digits);
  return v > 0 ? `+${s}` : v < 0 ? `−${s}` : s;
}

export function pnlClass(v: number | null | undefined): string {
  if (v === null || v === undefined || v === 0) return "";
  return v > 0 ? "pos" : "neg";
}

export function fmtTime(ts: string | null | undefined): string {
  if (!ts) return "—";
  try {
    return new Date(ts).toLocaleTimeString("pt-BR", {
      timeZone: "America/Sao_Paulo",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return ts;
  }
}

export function fmtDateTime(ts: string | null | undefined): string {
  // dd/mm hh:mm (America/Sao_Paulo)
  if (!ts) return "—";
  try {
    const d = new Date(ts);
    const date = d.toLocaleDateString("pt-BR", {
      timeZone: "America/Sao_Paulo",
      day: "2-digit",
      month: "2-digit",
    });
    const time = d.toLocaleTimeString("pt-BR", {
      timeZone: "America/Sao_Paulo",
      hour: "2-digit",
      minute: "2-digit",
    });
    return `${date} ${time}`;
  } catch {
    return ts;
  }
}

export function shortAddr(a: string | null | undefined): string {
  if (!a) return "—";
  return a.length > 12 ? `${a.slice(0, 6)}…${a.slice(-4)}` : a;
}

export function fmtNotional(
  size: number | null | undefined,
  price: number | null | undefined,
  digits = 2,
): string {
  if (
    size === null ||
    size === undefined ||
    price === null ||
    price === undefined ||
    Number.isNaN(size) ||
    Number.isNaN(price)
  ) {
    return "—";
  }
  return fmtNum(size * price, digits);
}

export const statusChip: Record<string, string> = {
  filled: "filled",
  partially_filled: "ack",
  acked: "ack",
  sent: "ack",
  created: "ack",
  cancelled: "dry",
  dry_run: "dry",
  rejected: "rej",
  error: "rej",
  active: "live",
  paused: "dry",
  auto_paused: "rej",
  draft: "dry",
  archived: "dry",
};
