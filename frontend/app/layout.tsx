import "./globals.css";import Nav from "@/components/Nav";
export const metadata={title:"Endurance AI",description:"Training-first endurance analytics and planning"};
export default function RootLayout({children}:{children:React.ReactNode}){return <html lang="en"><body><div className="shell"><Nav/><main className="main">{children}</main></div></body></html>}
