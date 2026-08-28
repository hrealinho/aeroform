"use client";
import {useCallback, useEffect, useState} from "react";
import Link from "next/link";
import {API} from "@/lib/api";
import {formatDuration} from "@/lib/datetime";
import ActivityStreamChart from "@/components/ActivityStreamChart";
import ZoneBars from "@/components/ZoneBars";

function km(m?:number|null){return m?`${(m/1000).toFixed(2)} km`:"-"}
function pace(distance?:number|null,seconds?:number|null){
  if(!distance||!seconds) return "-";
  const perKm=seconds/(distance/1000);
  return `${Math.floor(perKm/60)}:${`${Math.round(perKm%60)}`.padStart(2,"0")}/km`;
}
function n(v?:number|null,unit=""){return v==null?"-":`${Math.round(v)}${unit}`}

function Stat({label,value,sub}:{label:string;value:string;sub?:string}){
  return <div className="card"><div className="eyebrow">{label}</div><div className="metricBig">{value}</div>{sub&&<div className="muted">{sub}</div>}</div>;
}

export default function ActivityDetailClient({id}:{id:string}){
  const [a,setA]=useState<any>(null);
  const [error,setError]=useState("");

  const load=useCallback(async()=>{
    try{
      const r=await fetch(`${API}/activities/${id}`,{cache:"no-store"});
      if(!r.ok) throw new Error(r.status===404?"Activity not found":await r.text());
      setA(await r.json());
    }catch(e:any){setError(e?.message||"Could not load activity")}
  },[id]);
  useEffect(()=>{void load()},[load]);

  if(error) return <><Link href="/activities" className="muted">Back to activities</Link><div className="card section errorText">{error}</div></>;
  if(!a) return <p className="muted">Loading…</p>;

  const s=a.summary, l=a.load, t=a.terrain;
  const isRun=["running","trail_running","hiking","mountaineering"].includes(a.sport);

  return <>
    <Link href="/activities" className="muted">Back to activities</Link>
    <div className="row spread section">
      <div>
        <h1>{a.name||a.sport}</h1>
        <p className="muted">
          {new Date(a.start_time).toLocaleString()} · {a.sport.replace("_"," ")}
          {a.classification&&<> · classified {a.classification.confidence} confidence ({a.classification.reason})</>}
        </p>
      </div>
      <div className="row">
        {a.sources.map((src:any,i:number)=><span className="badge" key={i}>{src.type}{src.external_id?` #${src.external_id}`:""}</span>)}
      </div>
    </div>

    <div className="grid section planningMetrics">
      <Stat label="Duration" value={formatDuration(s.duration_s)} sub={s.moving_time_s?`${formatDuration(s.moving_time_s)} moving`:undefined}/>
      <Stat label="Distance" value={km(s.distance_m)} sub={isRun?pace(s.distance_m,s.moving_time_s):(s.avg_speed_mps?`${(s.avg_speed_mps*3.6).toFixed(1)} km/h`:undefined)}/>
      <Stat label="Elevation" value={s.elevation_gain_m!=null?`${Math.round(s.elevation_gain_m)} m`:"-"}
            sub={s.elevation_loss_m!=null?`${Math.round(s.elevation_loss_m)} m descent`:undefined}/>
      <Stat label="Load" value={n(l.training_load)} sub={`${l.method||"-"} · ${l.confidence||"-"}`}/>
    </div>

    <div className="grid section planningMetrics">
      <Stat label="Avg HR" value={n(s.avg_hr," bpm")} sub={s.max_hr?`max ${Math.round(s.max_hr)}`:undefined}/>
      <Stat label="Avg power" value={n(s.avg_power," W")} sub={s.normalized_power?`NP ${Math.round(s.normalized_power)} W`:undefined}/>
      <Stat label="Cadence" value={n(s.avg_cadence)} sub={isRun?"spm":"rpm"}/>
      <Stat label="Decoupling" value={a.aerobic_decoupling_pct!=null?`${a.aerobic_decoupling_pct}%`:"-"} sub="aerobic drift"/>
    </div>

    {a.streams.points.length>0&&<div className="card section">
      <div className="row spread"><div><h2>Streams</h2><div className="muted">{a.streams.sample_count.toLocaleString()} samples, downsampled to {a.streams.points.length} points.</div></div></div>
      <ActivityStreamChart points={a.streams.points} channels={a.streams.channels}/>
    </div>}

    {(a.zones.hr||a.zones.power)&&<div className="twoCol section">
      {a.zones.hr&&<div className="card"><h2>Time in heart-rate zones</h2><ZoneBars zones={a.zones.hr}/></div>}
      {a.zones.power&&<div className="card"><h2>Time in power zones</h2><ZoneBars zones={a.zones.power}/></div>}
    </div>}

    <div className="twoCol section">
      <div className="card"><h2>Load breakdown</h2>
        <div className="listRow"><span>Overall</span><b>{n(l.training_load)}</b></div>
        <div className="listRow"><span>Metabolic</span><b>{n(l.metabolic_load)}</b></div>
        {l.mechanical_load!=null&&<div className="listRow"><span>Mechanical</span><b>{n(l.mechanical_load)}</b></div>}
        {l.intensity_factor!=null&&<div className="listRow"><span>Intensity factor</span><b>{l.intensity_factor}</b></div>}
        {l.trimp!=null&&<div className="listRow"><span>TRIMP</span><b>{n(l.trimp)}</b></div>}
        {l.composite&&<p className="muted section">{l.composite.formula} — metabolic {l.composite.metabolic_weight}, mechanical {l.composite.mechanical_weight}</p>}
        {l.durations&&<p className="muted">Metabolic load used {formatDuration(l.durations.metabolic_s)} of moving time
          {l.durations.stopped_time_clamped&&<> · stopped time was clamped as implausible</>}</p>}
        <p className="muted">metric {l.metric_version}</p>
      </div>
      {t&&<div className="card"><h2>Terrain</h2>
        <div className="listRow"><span>Ascent load</span><b>{n(t.ascent_load)}</b></div>
        <div className="listRow"><span>Descent load</span><b>{n(t.descent_load)}</b></div>
        <div className="listRow"><span>Distance load</span><b>{n(t.distance_load)}</b></div>
        <div className="listRow"><span>Durability load</span><b>{n(t.durability_load)}</b></div>
        {t.gain_per_km!=null&&<div className="listRow"><span>Gain per km</span><b>{t.gain_per_km} m</b></div>}
        {a.sources.some((x:any)=>x.elevation_loss_source?.startsWith("estimated"))&&
          <p className="muted section">Descent is estimated, not measured. Enable stream syncing for measured descent.</p>}
      </div>}
    </div>

    <div className="card section"><h2>Laps</h2>
      {a.laps.length===0
        ? <p className="muted">No laps recorded for this activity. FIT and TCX files carry laps; a Strava summary-only import does not.</p>
        : <div className="activityTableWrap"><table className="table"><thead><tr>
            <th>#</th><th>Time</th><th>Distance</th><th>{isRun?"Pace":"Speed"}</th><th>Avg HR</th><th>Avg power</th><th>Elev</th><th>Trigger</th>
          </tr></thead><tbody>{a.laps.map((lap:any)=><tr key={lap.index}>
            <td>{lap.name||lap.index+1}</td>
            <td>{formatDuration(lap.moving_s||lap.elapsed_s)}</td>
            <td>{km(lap.distance_m)}</td>
            <td>{isRun?(lap.pace||"-"):(lap.avg_speed_mps?`${(lap.avg_speed_mps*3.6).toFixed(1)} km/h`:"-")}</td>
            <td>{n(lap.avg_hr)}</td>
            <td>{lap.avg_power?`${Math.round(lap.avg_power)} W`:"-"}</td>
            <td>{lap.elevation_gain_m!=null?`${Math.round(lap.elevation_gain_m)} m`:"-"}</td>
            <td className="muted">{lap.trigger||"-"}</td>
          </tr>)}</tbody></table></div>}
    </div>
  </>;
}
