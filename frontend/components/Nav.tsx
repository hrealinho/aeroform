"use client";
import Link from "next/link";
import {usePathname} from "next/navigation";

const LINKS=[
  {href:"/",label:"Dashboard"},
  {href:"/calendar",label:"Calendar"},
  {href:"/season",label:"Season"},
  {href:"/activities",label:"Activities"},
  {href:"/power",label:"Power"},
  {href:"/thresholds",label:"Thresholds"},
  {href:"/imports",label:"Imports"},
  {href:"/coach",label:"Coach"},
];

export default function Nav(){
  const pathname=usePathname();
  return <aside className="sidebar">
    <div className="brand">Aeroform</div>
    <nav className="nav">
      {LINKS.map(l=>{
        const active=l.href==="/"?pathname==="/":pathname.startsWith(l.href);
        return <Link key={l.href} href={l.href} className={active?"active":""} aria-current={active?"page":undefined}>{l.label}</Link>;
      })}
    </nav>
  </aside>;
}
