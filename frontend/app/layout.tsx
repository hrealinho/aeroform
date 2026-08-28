import "./globals.css";import Nav from "@/components/Nav";
export const metadata={
  title:{default:"Aeroform",template:"%s · Aeroform"},
  description:"Training-first endurance analytics, season planning and grounded coaching.",
};
export default function RootLayout({children}:{children:React.ReactNode}){return <html lang="en"><body><div className="shell"><Nav/><main className="main">{children}</main></div></body></html>}
