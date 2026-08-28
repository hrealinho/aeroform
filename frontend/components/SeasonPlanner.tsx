"use client";
import {useCallback, useEffect, useState} from "react";
import {API} from "@/lib/api";
import {formatDuration} from "@/lib/datetime";

/**
 * The layer between an objective and individual workouts.
 *
 * Preview first, apply second: a season plan replaces training blocks, so it is never
 * created from a single click.
 */
export default function SeasonPlanner(){
  const [plan,setPlan]=useState<any>(null);
  const [progress,setProgress]=useState<any>(null);
  const [busy,setBusy]=useState("");
  const [error,setError]=useState("");
  const [weeks,setWeeks]=useState(4);

  const loadProgress=useCallback(async()=>{
    const r=await fetch(`${API}/analytics/block-progress`,{cache:"no-store"});
    if(r.ok) setProgress(await r.json());
  },[]);
  useEffect(()=>{void loadProgress()},[loadProgress]);

  async function call(path:string,body:any,tag:string){
    setBusy(tag);setError("");
    try{
      const r=await fetch(`${API}${path}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
      const j=await r.json();
      if(!r.ok) throw new Error(typeof j.detail==="string"?j.detail:JSON.stringify(j.detail));
      return j;
    }catch(e:any){setError(e?.message||"Request failed");return null}
    finally{setBusy("")}
  }

  const preview=async()=>setPlan(await call("/coach/plan-season",{apply:false},"preview"));
  const apply=async()=>{const j=await call("/coach/plan-season",{apply:true},"apply");if(j){setPlan(j);await loadProgress()}};
  const generate=async()=>{const j=await call("/coach/generate-block",{weeks},"generate");if(j)setError(`Proposal ${j.id} created with ${j.commands.length} sessions. Approve it on the Coach page.`)};

  return <>
    <div className="card section">
      <div className="row spread">
        <div>
          <h2>Season plan</h2>
          <p className="muted">Works backwards from your next A race: how many weeks of base, build, specific, peak and taper fit, and what weekly load each should carry. Targets ramp from your own recent load.</p>
        </div>
        <div className="row">
          <button className="button secondary" disabled={!!busy} onClick={preview}>{busy==="preview"?"Building…":"Preview plan"}</button>
          <button className="button" disabled={!!busy} onClick={apply}>{busy==="apply"?"Applying…":"Apply plan"}</button>
        </div>
      </div>
      {error&&<p className="errorText section">{error}</p>}

      {plan&&<div className="section">
        <div className="row">
          <span className="badge">{plan.objective.name} · {plan.objective.date}</span>
          <span className="badge">{plan.total_weeks} weeks</span>
          <span className="badge">{plan.method}</span>
          {plan.applied.length>0&&<span className="badge success">{plan.applied.length} blocks created</span>}
        </div>
        <p className="muted section">{plan.basis.note}</p>
        <div className="activityTableWrap section"><table className="table"><thead><tr>
          <th>Phase</th><th>Weeks</th><th>From</th><th>To</th><th>Mean load</th><th>Peak load</th><th>Mean hours</th>
        </tr></thead><tbody>{plan.blocks.map((b:any)=><tr key={b.start_date}>
          <td><b>{b.block_type}</b></td><td>{b.weeks}</td><td>{b.start_date}</td><td>{b.end_date}</td>
          <td>{b.targets.weekly_load}</td><td>{b.targets.peak_weekly_load}</td>
          <td>{formatDuration(b.targets.weekly_hours*3600)}</td>
        </tr>)}</tbody></table></div>
      </div>}
    </div>

    <div className="card section">
      <div className="row spread">
        <div><h2>Generate workouts</h2><p className="muted">Fills several weeks at once, each using its periodised target so progression, recovery weeks and the taper survive. Creates a proposal for you to approve.</p></div>
        <div className="row">
          <select className="input" value={weeks} onChange={e=>setWeeks(Number(e.target.value))}>
            {[1,2,4,8,12].map(w=><option key={w} value={w}>{w} week{w>1?"s":""}</option>)}
          </select>
          <button className="button" disabled={!!busy} onClick={generate}>{busy==="generate"?"Generating…":"Generate"}</button>
        </div>
      </div>
    </div>

    {progress?.blocks?.length>0&&<div className="card section">
      <h2>Target vs planned vs actual</h2>
      <p className="muted">Where the plan, the calendar and completed training meet.</p>
      {progress.blocks.map((b:any)=><div className="section" key={b.id}>
        <div className="row spread">
          <div className="row">
            <b>{b.block_type}</b><span className="muted">{b.start_date} - {b.end_date}</span>
            {b.is_current&&<span className="badge success">current</span>}
          </div>
        </div>
        <div className="activityTableWrap"><table className="table"><thead><tr>
          <th>Week</th><th>Phase</th><th>Target</th><th>Planned</th><th>Actual</th><th>Note</th>
        </tr></thead><tbody>{b.weeks.map((w:any)=><tr key={w.week_start}>
          <td>{w.week_start}</td>
          <td>{w.is_recovery?<span className="badge">recovery</span>:w.phase}</td>
          <td>{w.target_load}</td>
          <td>{w.planned_load}{w.planned_vs_target_pct!=null&&<span className="muted"> · {w.planned_vs_target_pct}%</span>}</td>
          <td>{w.is_past||w.actual_load>0?<>{w.actual_load}{w.actual_vs_target_pct!=null&&<span className="muted"> · {w.actual_vs_target_pct}%</span>}</>:"-"}</td>
          <td className="muted">{w.note}</td>
        </tr>)}</tbody></table></div>
      </div>)}
    </div>}
    {progress&&progress.blocks.length===0&&<div className="card section muted">{progress.note}</div>}
  </>;
}
