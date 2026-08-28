import Link from "next/link";
export default function Nav(){return <aside className="sidebar"><div className="brand">Aeroform</div><nav className="nav"><Link href="/">Dashboard</Link><Link href="/calendar">Calendar</Link><Link href="/season">Season</Link><Link href="/activities">Activities</Link><Link href="/imports">Imports</Link><Link href="/coach">Coach</Link></nav></aside>}
