import "./globals.css";
import { Shell } from "../components/shell";

export const metadata = { title: "Lorebound", description: "A persistent role-playing world — a living fantasy chronicle." };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
