"use client";
import {useCallback, useEffect, useState} from "react";
import {API} from "@/lib/api";
import {formatDuration} from "@/lib/datetime";
import PowerCurveChart from "@/components/PowerCurveChart";

const PERIODS=[{d:42,label:"6 weeks"},{d:90,label:"3 months"},{d:365,label:"1 year"},{d:1095,label:"3 years"}];
// The durations a rider quotes, rather than every point on the curve.
const KEY_EFFORTS=[5,15,60,300,1200,3600];

export default function PowerClient(){
  const [days,setDays]=useState(365);
  const [profile,setProfile]=useState<any>(null);
  const [races,setRaces]=useState<any>(null);
  const [weight,setWeight]=useState("");
  const [error,setError]=useState("");
  const [busy,setBusy]=useState(false);

  const load=useCallback(async()=>{
    setError("");
    try{
      const [p,r]=await Promise.all([
        fetch(`${API}/power/profile?days=${days}&compare_days=${days*2}`,{cache:"no-store"}).then(x=>x.json()),
        fetch(`${API}/running/predictions?days=${days}`,{cache:"no-store"}).then(x=>x.json()),
      ]);
      setProfile(p);setRaces(r);
    }catch(e:any){setError(e?.message||"Could not load the power profile")}
  },[days]);
  useEffect(()=>{void load()},[load]);

  async function saveWeight(e:React.FormEvent){
    e.preventDefault();setBusy(true);setError("");
    try{
      const r=await fetch(`${API}/thresholds/manual`,{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({sport:"global",metric:"weight_kg",value:Number(weight)})});
      if(!r.ok) throw new Error(await r.text());
      setWeight("");await load();
    }catch(e:any){setError(e?.message||"Could not save weight")}finally{setBusy(false)}
  }

  const points=profile?.points||[];
  const byDuration=new Map<number,any>(points.map((p:any)=>[p.duration_s,p]));
  const cp=profile?.critical_power;
  const rt=profile?.rider_type;

  return <>
    <div className="row spread">
      <div><h1>Power profile</h1><p className="muted">Your best sustained power at every duration, and what it implies.</p></div>
      <div className="row">{PERIODS.map(p=>
        <button key={p.d} className={days===p.d?"button":"button secondary"} onClick={()=>setDays(p.d)}>{p.label}</button>)}
      </div>
    </div>
    {error&&<div className="card section errorText">{error}</div>}
    {profile?.advice&&<div className="warning section high"><strong>No curve yet</strong><span>{profile.advice}</span></div>}
    {profile?.weight_advice&&<form className="card section" onSubmit={saveWeight}>
      <b>Add your weight for watts per kilo</b><p className="muted">{profile.weight_advice}</p>
      <div className="row"><input className="input" type="number" step="0.1" min="30" max="200" placeholder="kg"
        value={weight} onChange={e=>setWeight(e.target.value)}/>
        <button className="button" disabled={busy||!weight}>Save weight</button></div>
    </form>}

    {points.length>0&&<>
      <div className="grid section planningMetrics">
        {KEY_EFFORTS.map(s=>{const p=byDuration.get(s);return <div className="card" key={s}>
          <div className="eyebrow">{formatDuration(s)}</div>
          <div className="metricBig">{p?`${Math.round(p.watts)} W`:"-"}</div>
          {p?.w_per_kg!=null&&<small className="muted">{p.w_per_kg} W/kg</small>}
        </div>})}
      </div>

      <div className="card section">
        <h2>Power duration curve</h2>
        <p className="muted">Best mean power for each duration. The dashed line is the preceding period, for comparison.</p>
        <PowerCurveChart points={points} compareLabel="Previous period"/>
      </div>

      <div className="twoCol section">
        <div className="card"><h2>Critical power</h2>
          {cp?<>
            <div className="metric">{cp.critical_power_w} W</div>
            <p className="muted">W&prime; {Math.round(cp.w_prime_j/1000)} kJ · {cp.points_used} efforts · confidence {cp.confidence}</p>
            <p className="muted">{cp.caveat}</p>
          </>:<p className="muted">Needs at least two sustained efforts between 2 and 20 minutes in this period.</p>}
        </div>
        <div className="card"><h2>Rider type</h2>
          {rt?<>
            <div className="metricBig">{rt.type}</div>
            <p className="muted">{rt.reason}</p>
            <div className="row section">
              <span className="badge">sprint / 5min {rt.sprint_to_vo2_ratio ?? "-"}</span>
              <span className="badge">20min / 5min {rt.threshold_to_vo2_ratio ?? "-"}</span>
            </div>
            <p className="muted section">{rt.caveat}</p>
          </>:<p className="muted">Needs a 5-minute effort in this period to compare against.</p>}
        </div>
      </div>

      <div className="card section"><h2>All efforts</h2>
        <div className="activityTableWrap"><table className="table"><thead><tr>
          <th>Duration</th><th>Best</th><th>W/kg</th><th>Previous</th><th>Change</th><th>Set</th>
        </tr></thead><tbody>{points.map((p:any)=>{
          const delta=p.previous_watts!=null?p.watts-p.previous_watts:null;
          return <tr key={p.duration_s}>
            <td>{formatDuration(p.duration_s)}</td><td><b>{Math.round(p.watts)} W</b></td>
            <td>{p.w_per_kg ?? "-"}</td><td>{p.previous_watts!=null?`${Math.round(p.previous_watts)} W`:"-"}</td>
            <td>{delta!=null?`${delta>=0?"+":""}${Math.round(delta)} W`:"-"}</td>
            <td className="muted">{p.date||"-"}</td>
          </tr>})}</tbody></table></div>
      </div>
    </>}

    {races?.has_data&&<div className="card section">
      <h2>Running race predictions</h2>
      <p className="muted">{races.method_note}</p>
      <div className="activityTableWrap"><table className="table"><thead><tr>
        <th>Distance</th><th>Predicted</th><th>Pace</th><th>From threshold pace</th><th>Confidence</th><th>Based on</th>
      </tr></thead><tbody>{races.predictions.map((p:any)=><tr key={p.distance}>
        <td>{p.distance}</td><td><b>{p.riegel_time}</b></td><td>{p.riegel_pace}</td>
        <td>{p.critical_speed_time||"-"}</td>
        <td><span className={`badge ${p.confidence==="high"?"success":""}`}>{p.confidence}</span></td>
        <td className="muted">{(p.based_on.distance_m/1000).toFixed(1)} km in {p.based_on.time} · {p.based_on.date}</td>
      </tr>)}</tbody></table></div>
      {races.predictions.some((p:any)=>p.caveat)&&<div className="warningStack section">
        {races.predictions.filter((p:any)=>p.caveat).map((p:any)=>
          <div className="warning" key={p.distance}><strong>{p.distance}</strong><span>{p.caveat}</span></div>)}
      </div>}
    </div>}
    {races&&!races.has_data&&<div className="card section muted">{races.note}</div>}
  </>;
}
