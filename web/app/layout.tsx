import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "MTS Support | Sarah",
  description: "Call or message Sarah for Manage Tech Services support.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
