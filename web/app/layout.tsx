import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "trade · Tokio",
  description: "Operação do engine de trades Tokio",
};

// UPDATE-0078: sem viewport meta o mobile renderizava "estourado"/com zoom (o
// browser assumia uma largura de desktop). Ancorar na largura do dispositivo.
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
};

const themeInit = `
try {
  if (localStorage.getItem("tokio-theme") === "light") {
    document.documentElement.classList.add("light");
  }
} catch {}
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="pt-BR" suppressHydrationWarning>
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link
          href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap"
          rel="stylesheet"
        />
        <script dangerouslySetInnerHTML={{ __html: themeInit }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
