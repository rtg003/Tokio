import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "trade · Tokio",
  description: "Operação do engine de trades Tokio",
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
