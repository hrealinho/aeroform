"use client";
import {useEffect,useState} from "react";
import Link from "next/link";
import {API} from "@/lib/api";
import {formatDuration} from "@/lib/datetime";

const SPORTS=["running","trail_running","cycling","hiking","mountaineering","climbing","other"];

export default function ActivitiesClient(){
  const [activities,setActivities]=useState<any[]>([]);
  const [sport,setSport]=useState("");
  const [busy,setBusy]=useState<number|null>(null);
  const [error,setError]=useState("");
  const [dupes,setDupes]=useState<any>({groups:[],group_count:0,duplicated_load:0});
  const [showDupes,setShowDupes]=useState(false);

  async function load(){
    const q=sport?`?limit=300&sport=${sport}`:"?limit=300";
    const r=await fetch(API+"/activities"+q,{cache:"no-store"});
    if(!r.ok) throw new Error(await r.text());
    setActivities(await r.json());
  }
  async function loadDupes(){
    const r=await fetch(API+"/activities/duplicates",{cache:"no-store"});
    if(r.ok) setDupes(await r.json());
  }
  useEffect(()=>{load().catch(e=>setError(String(e)))},[sport]);
  useEffect(()=>{void loadDupes()},[]);

  // Never merged automatically: each copy has its own provider id, so which one to keep
  // is the athlete's call.
  async function removeActivity(id:number){
    setBusy(id);setError("");
    try{
      const r=await fetch(`${API}/activities/${id}`,{method:"DELETE"});
      if(!r.ok) throw new Error(await r.text());
      await Promise.all([load(),loadDupes()]);
    }catch(e){setError(String(e))}finally{setBusy(null)}
  }

  async function reclassify(id:number,newSport:string){
    setBusy(id);setError("");
    try{
      const r=await fetch(`${API}/activities/${id}/classification`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({sport:newSport})});
      if(!r.ok) throw new Error(await r.text());
      const updated=await r.json();
      setActivities(items=>items.map(a=>a.id===id?updated:a));
    }catch(e){setError(String(e))}finally{setBusy(null)}
  }

  return <><div className="row spread"><div><h1>Activities</h1><p className="muted">Sport-aware load with metabolic, mechanical, ascent, descent and durability components.</p></div><select className="input" value={sport} onChange={e=>setSport(e.target.value)}><option value="">All sports</option>{SPORTS.map(s=><option key={s}>{s}</option>)}</select></div>
    {error&&<p className="errorText">{error}</p>}
    {dupes.group_count>0&&<div className="card section"><div className="row spread"><div><b>{dupes.group_count} possible duplicate session{dupes.group_count===1?"":"s"}</b><p className="muted">Same start time, duration and distance - usually one session uploaded twice. They are counted twice in your load ({dupes.duplicated_load} load double-counted). Nothing is merged automatically.</p></div><button className="button secondary" onClick={()=>setShowDupes(v=>!v)}>{showDupes?"Hide":"Review"}</button></div>
      {showDupes&&<div className="section">{dupes.groups.map((g:any,i:number)=><div className="listRow" key={i}><div>
        <div className="muted">{g.reasons.join(" · ")}</div>
        {g.activities.map((a:any)=><div className="row spread" key={a.id}><span>{new Date(a.start_time).toLocaleString()} · {a.sport.replace("_"," ")} · {a.name||"-"} · {a.distance_m?(a.distance_m/1000).toFixed(2)+" km":"-"} · load {a.training_load}</span><button disabled={busy===a.id} onClick={()=>removeActivity(a.id)}>Delete this copy</button></div>)}
      </div></div>)}</div>}
    </div>}
    <div className="card section activityTableWrap"><table className="table"><thead><tr><th>Date</th><th>Sport</th><th>Duration</th><th>Distance</th><th>Vertical</th><th>Overall</th><th>Metabolic</th><th>Mechanical</th><th>Downhill</th><th>Method</th></tr></thead><tbody>{activities.map(a=>{
      const confidence=a.classification?.confidence;
      return <tr key={a.id}><td><Link href={`/activities/${a.id}`} className="rowLink">{new Date(a.start_time).toLocaleDateString()}</Link></td><td><div className="classificationCell"><select className="input compact" disabled={busy===a.id} value={a.sport} onChange={e=>reclassify(a.id,e.target.value)}>{SPORTS.map(s=><option key={s}>{s}</option>)}</select>{confidence&&<small className="muted">{confidence}: {a.classification?.reason}</small>}</div></td><td><Link href={`/activities/${a.id}`} className="rowLink">{formatDuration(a.duration_s)}</Link></td><td>{a.distance_m?(a.distance_m/1000).toFixed(1)+" km":"-"}</td><td>{a.elevation_gain_m!=null||a.elevation_loss_m!=null?<><span>↑{a.elevation_gain_m!=null?Math.round(a.elevation_gain_m)+" m":"-"}</span><br/><span>↓{a.elevation_loss_m!=null?Math.round(a.elevation_loss_m)+" m":"-"}</span></>:"-"}</td><td><b>{a.training_load?.toFixed?.(0)??"-"}</b></td><td>{a.metabolic_load?.toFixed?.(0)??"-"}</td><td>{a.mechanical_load?.toFixed?.(0)??"-"}</td><td>{a.descent_load?.toFixed?.(0)??"-"}</td><td><small>{a.load_method||"-"}<br/>{a.load_confidence||"-"}</small></td></tr>
    })}</tbody></table></div></>;
}
