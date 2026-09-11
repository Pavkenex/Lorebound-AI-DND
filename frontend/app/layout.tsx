export const metadata = { title: "Lorebound", description: "A persistent role-playing world." };
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body style={{ background: "#14100c", color: "#e8dcc3", margin: 0 }}>{children}</body>
    </html>
  );
}
