"use client";
import {useEffect,useState} from "react";
import {API} from "@/lib/api";

function fmt(v:any){
  if(v.display_value) return v.display_value;
  if(v.metric.includes("hr")) return `${Math.round(v.value)} bpm`;
  if(v.metric==="ftp"||v.metric==="critical_power") return `${Math.round(v.value)} W`;
  return String(Math.round(v.value*100)/100);
}
function zoneRange(z:any){
  if(z.unit==="pace"){
    if(!z.faster_than) return `slower than ${z.slower_than}`;
    if(!z.slower_than) return `faster than ${z.faster_than}`;
    return `${z.slower_than} to ${z.faster_than}`;
  }
  if(z.min==null) return `< ${Math.round(z.max)} ${z.unit}`;
  if(z.max==null) return `≥ ${Math.round(z.min)} ${z.unit}`;
  return `${Math.round(z.min)}–${Math.round(z.max)} ${z.unit}`;
}
export default function ThresholdsClient(){
  const [data,setData]=useState<any>({thresholds:[]}); const [busy,setBusy]=useState(false); const [error,setError]=useState("");
  const [days,setDays]=useState(365); const [backfill,setBackfill]=useState(true);
  const [manual,setManual]=useState<any>({sport:"cycling",metric:"ftp",value:""});
  const [note,setNote]=useState("");
  async function load(){const r=await fetch(API+"/thresholds",{cache:"no-store"});if(!r.ok)throw new Error(await r.text());setData(await r.json())}
  useEffect(()=>{load().catch(e=>setError(String(e)))},[]);
  function describeResult(j:any){
    // The backfill now runs as a background task, so say so rather than implying the
    // numbers on screen have already been recalculated.
    const from=j.effective_from?` Applies from ${j.effective_from}.`:"";
    const task=j.recompute_task_id?" Historical load is being recalculated in the background - reload in a moment to see updated activities.":"";
    setNote(`${from}${task}`.trim());
  }
  async function estimate(){setBusy(true);setError("");setNote("");try{const r=await fetch(API+"/thresholds/estimate",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({history_days:days,persist:true,apply_to_history:backfill})});if(!r.ok)throw new Error(await r.text());const j=await r.json();setData(j);describeResult(j)}catch(e){setError(String(e))}finally{setBusy(false)}}
  async function saveManual(){setBusy(true);setError("");setNote("");try{const r=await fetch(API+"/thresholds/manual",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({...manual,value:Number(manual.value)})});if(!r.ok)throw new Error(await r.text());const j=await r.json();setData(j);describeResult(j);setManual({...manual,value:""})}catch(e){setError(String(e))}finally{setBusy(false)}}
  async function removeThreshold(id:number){setBusy(true);setError("");setNote("");try{const r=await fetch(`${API}/thresholds/${id}`,{method:"DELETE"});if(!r.ok)throw new Error(await r.text());const j=await r.json();setData(j);describeResult(j)}catch(e){setError(String(e))}finally{setBusy(false)}}
  const metricsBySport:any={cycling:["ftp","threshold_hr","max_hr","resting_hr"],running:["critical_power","threshold_speed_mps","threshold_hr","max_hr","resting_hr"],trail_running:["threshold_hr","max_hr","resting_hr"],global:["max_hr","resting_hr"]};
  return <><h1>Thresholds & Zones</h1><p className="muted">Automatically estimate FTP, running critical power, threshold pace and lactate-threshold HR from your activity history. Manual values always take precedence.</p>
  <div className="card section"><div className="row spread"><div><b>Estimate from history</b><p className="muted">Long sustained efforts are preferred over short peaks. Estimates include confidence and remain editable.</p></div><div className="row"><select className="input" value={days} onChange={e=>setDays(Number(e.target.value))}><option value={90}>90 days</option><option value={180}>180 days</option><option value={365}>1 year</option><option value={730}>2 years</option></select><label><input type="checkbox" checked={backfill} onChange={e=>setBackfill(e.target.checked)}/> recompute selected history</label><button className="button" disabled={busy} onClick={estimate}>{busy?"Estimating…":"Estimate thresholds"}</button></div></div>{error&&<p className="errorText">{error}</p>}{note&&<p className="muted">{note}</p>}</div>
  <div className="card section"><b>Manual override</b><p className="muted">Use a lab/field-test value when you know it. For threshold pace, enter speed in m/s for now; the displayed threshold and zones are converted to min/km.</p><div className="row"><select className="input" value={manual.sport} onChange={e=>{const sport=e.target.value;setManual({sport,metric:metricsBySport[sport][0],value:""})}}>{Object.keys(metricsBySport).map(s=><option key={s}>{s}</option>)}</select><select className="input" value={manual.metric} onChange={e=>setManual({...manual,metric:e.target.value})}>{metricsBySport[manual.sport].map((m:string)=><option key={m}>{m}</option>)}</select><input className="input" type="number" step="0.01" placeholder="Value" value={manual.value} onChange={e=>setManual({...manual,value:e.target.value})}/><button className="button" disabled={busy||!manual.value} onClick={saveManual}>Save manual value</button></div></div>
  {(data.advice||[]).length>0&&<div className="card section"><b>What is limiting your load model</b><p className="muted">Load falls back to a duration estimate when the physiology it needs is missing.</p>
    {(data.advice||[]).map((a:any,i:number)=><div className={`warning ${a.severity==="high"?"high":""}`} key={i}><strong>{a.metric.replaceAll("_"," ")}</strong><span>{a.message} <em>{a.action}</em>{a.auto_estimable===false&&<small className="muted"> (cannot be estimated automatically from your data)</small>}</span></div>)}
  </div>}
  <div className="grid2">{(data.thresholds||[]).map((t:any)=><div className="card section" key={`${t.sport}-${t.metric}`}><div className="row spread"><div><div className="eyebrow">{t.sport}</div><h2>{t.metric.replaceAll("_"," ")}</h2></div><div className="metricBig">{fmt(t)}</div></div><div className="row spread"><p className="muted">{t.source} · confidence {t.confidence} · from {t.valid_from}</p><button className="button secondary" disabled={busy} onClick={()=>removeThreshold(t.id)}>Remove</button></div>{t.caveat&&<p className="muted"><em>{t.caveat}</em></p>}{t.zones&&<table className="table"><thead><tr><th>Zone</th><th>Range</th></tr></thead><tbody>{t.zones.map((z:any)=><tr key={z.label}><td>{z.label}</td><td>{zoneRange(z)}</td></tr>)}</tbody></table>}</div>)}</div></>
}
