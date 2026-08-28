"use client";
import {useEffect,useState} from "react";
import FitnessChart from "./FitnessChart";
import {API} from "@/lib/api";

const KINDS=["overall","metabolic","mechanical","ascent","descent","durability"];
const SPORTS=["","running","trail_running","cycling","hiking","mountaineering"];

export default function LoadExplorer(){
  const [kind,setKind]=useState("overall");const [sport,setSport]=useState("");const [data,setData]=useState<any[]>([]);const [error,setError]=useState("");
  useEffect(()=>{const q=new URLSearchParams({load_kind:kind});if(sport)q.set("sport",sport);fetch(`${API}/analytics/fitness?${q}`).then(async r=>{if(!r.ok)throw new Error(await r.text());return r.json()}).then(setData).catch(e=>setError(String(e)))},[kind,sport]);
  return <div><div className="row spread"><div><h2>Load explorer</h2><div className="muted">Apply the same fitness/fatigue model to overall, metabolic or terrain-specific load.</div></div><div className="row"><select className="input" value={sport} onChange={e=>setSport(e.target.value)}>{SPORTS.map(s=><option key={s} value={s}>{s||"All sports"}</option>)}</select><select className="input" value={kind} onChange={e=>setKind(e.target.value)}>{KINDS.map(k=><option key={k}>{k}</option>)}</select></div></div>{error&&<p className="errorText">{error}</p>}<FitnessChart data={data}/></div>
}
