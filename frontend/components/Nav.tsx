"use client";
import Link from "next/link";
import {usePathname} from "next/navigation";
import Icon from "./Icon";
import Logo from "./Logo";

const LINKS=[
  {href:"/",icon:"dashboard",label:"Dashboard"},
  {href:"/calendar",icon:"calendar",label:"Calendar"},
  {href:"/season",icon:"season",label:"Season"},
  {href:"/activities",icon:"activities",label:"Activities"},
  {href:"/power",icon:"power",label:"Power"},
  {href:"/thresholds",icon:"thresholds",label:"Thresholds"},
  {href:"/imports",icon:"imports",label:"Imports"},
  {href:"/coach",icon:"coach",label:"Coach"},
];

export default function Nav(){
  const pathname=usePathname();
  return <aside className="sidebar">
    <div className="brand"><Logo/> Aeroform</div>
    <nav className="nav">
      {LINKS.map(l=>{
        const active=l.href==="/"?pathname==="/":pathname.startsWith(l.href);
        return <Link key={l.href} href={l.href} className={active?"active":""} aria-current={active?"page":undefined}>
          <Icon name={l.icon}/>{l.label}
        </Link>;
      })}
    </nav>
  </aside>;
}
