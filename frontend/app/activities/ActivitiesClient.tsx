"use client";
import {useEffect,useState} from "react";
import {API} from "@/lib/api";

const SPORTS=["running","trail_running","cycling","hiking","mountaineering","climbing","other"];

export default function ActivitiesClient(){
  const [activities,setActivities]=useState<any[]>([]);
  const [sport,setSport]=useState("");
  const [busy,setBusy]=useState<number|null>(null);
  const [error,setError]=useState("");

  async function load(){
    const q=sport?`?limit=300&sport=${sport}`:"?limit=300";
    const r=await fetch(API+"/activities"+q,{cache:"no-store"});
    if(!r.ok) throw new Error(await r.text());
    setActivities(await r.json());
  }
  useEffect(()=>{load().catch(e=>setError(String(e)))},[sport]);

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
    <div className="card section activityTableWrap"><table className="table"><thead><tr><th>Date</th><th>Sport</th><th>Duration</th><th>Distance</th><th>Vertical</th><th>Overall</th><th>Metabolic</th><th>Mechanical</th><th>Downhill</th><th>Method</th></tr></thead><tbody>{activities.map(a=>{
      const confidence=a.classification?.confidence;
      return <tr key={a.id}><td>{new Date(a.start_time).toLocaleDateString()}</td><td><div className="classificationCell"><select className="input compact" disabled={busy===a.id} value={a.sport} onChange={e=>reclassify(a.id,e.target.value)}>{SPORTS.map(s=><option key={s}>{s}</option>)}</select>{confidence&&<small className="muted">{confidence}: {a.classification?.reason}</small>}</div></td><td>{(a.duration_s/3600).toFixed(1)} h</td><td>{a.distance_m?(a.distance_m/1000).toFixed(1)+" km":"-"}</td><td>{a.elevation_gain_m!=null||a.elevation_loss_m!=null?<><span>↑{a.elevation_gain_m!=null?Math.round(a.elevation_gain_m)+" m":"-"}</span><br/><span>↓{a.elevation_loss_m!=null?Math.round(a.elevation_loss_m)+" m":"-"}</span></>:"-"}</td><td><b>{a.training_load?.toFixed?.(0)??"-"}</b></td><td>{a.metabolic_load?.toFixed?.(0)??"-"}</td><td>{a.mechanical_load?.toFixed?.(0)??"-"}</td><td>{a.descent_load?.toFixed?.(0)??"-"}</td><td><small>{a.load_method||"-"}<br/>{a.load_confidence||"-"}</small></td></tr>
    })}</tbody></table></div></>;
}
