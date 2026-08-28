"use client";

import {useCallback, useEffect, useMemo, useState} from "react";
import {API} from "@/lib/api";
import {formatDuration} from "@/lib/datetime";

type Evidence={metric:string;value:any;period?:string};
type Message={id:number;role:"user"|"assistant";content:string;evidence:Evidence[];created_at:string};
type Command={action:string;workout_id?:number;scheduled_at?:string;sport?:string;name?:string;duration_s?:number;intensity?:string;reason?:string};
type Proposal={id:number;proposal_type:string;status:string;title:string;summary:string;commands:Command[];validation:{valid?:boolean;errors?:string[];warnings?:{message:string;severity?:string}[];before_weeks?:any[];after_weeks?:any[]};provider:string;created_at:string};
type Context={history:{activity_count:number;first_activity?:string;last_activity?:string};state:{fitness:number;fatigue:number;form:number;load_7d:number;load_28d:number;typical_weekly_load_8w:number;typical_weekly_hours_8w:number};objectives:any[];current_block?:any;adherence_28d_pct?:number|null};
type Profile={available_hours_per_week?:number|null;preferred_long_day?:number|null;preferred_rest_day?:number|null;doubles_allowed:boolean;preferences:Record<string,any>};

const DAYS=["Mon","Tue","Wed","Thu","Fri","Sat","Sun"];
const hours=formatDuration;
function commandLabel(c:Command){
  if(c.action==="create_workout") return `Add ${c.name||c.sport} · ${c.scheduled_at?.slice(0,10)} · ${hours(c.duration_s)} · ${c.intensity||""}`;
  if(c.action==="move_workout") return `Move workout #${c.workout_id} → ${c.scheduled_at?.slice(0,10)}`;
  if(c.action==="delete_workout") return `Delete workout #${c.workout_id}`;
  return `Modify workout #${c.workout_id}${c.duration_s?` · ${hours(c.duration_s)}`:""}${c.intensity?` · ${c.intensity}`:""}`;
}

export default function CoachClient(){
  const [context,setContext]=useState<Context|null>(null);
  const [profile,setProfile]=useState<Profile>({available_hours_per_week:null,preferred_long_day:5,preferred_rest_day:0,doubles_allowed:false,preferences:{}});
  const [messages,setMessages]=useState<Message[]>([]);
  const [proposals,setProposals]=useState<Proposal[]>([]);
  const [question,setQuestion]=useState("");
  const [busy,setBusy]=useState("");
  const [error,setError]=useState("");

  const load=useCallback(async()=>{
    setError("");
    try{
      const [ctx,p,msg,prop]=await Promise.all([
        fetch(`${API}/coach/context`,{cache:"no-store"}).then(r=>r.ok?r.json():Promise.reject(new Error("Context failed"))),
        fetch(`${API}/coach/profile`,{cache:"no-store"}).then(r=>r.ok?r.json():Promise.reject(new Error("Profile failed"))),
        fetch(`${API}/coach/messages?limit=40`,{cache:"no-store"}).then(r=>r.ok?r.json():Promise.reject(new Error("Messages failed"))),
        fetch(`${API}/coach/proposals?limit=20`,{cache:"no-store"}).then(r=>r.ok?r.json():Promise.reject(new Error("Proposals failed")))
      ]);
      setContext(ctx);setProfile(p);setMessages(msg);setProposals(prop);
    }catch(e:any){setError(e.message||"Failed to load coach")}
  },[]);
  useEffect(()=>{load()},[load]);

  async function ask(e:React.FormEvent){
    e.preventDefault(); if(!question.trim())return;
    setBusy("ask");setError("");
    const q=question;setQuestion("");
    try{
      const r=await fetch(`${API}/coach/ask`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({question:q})});
      if(!r.ok)throw new Error(await r.text());
      await load();
    }catch(e:any){setError(e.message||"Coach request failed")}finally{setBusy("")}
  }
  async function generate(){
    setBusy("generate");setError("");
    try{const r=await fetch(`${API}/coach/generate-week`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({strategy:"balanced"})});if(!r.ok)throw new Error(await r.text());await load()}catch(e:any){setError(e.message||"Plan generation failed")}finally{setBusy("")}
  }
  async function adapt(){
    setBusy("adapt");setError("");
    try{const r=await fetch(`${API}/coach/adapt-week`,{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});if(!r.ok)throw new Error(await r.text());await load()}catch(e:any){setError(e.message||"Adaptation failed")}finally{setBusy("")}
  }
  async function decide(id:number,action:"approve"|"reject"){
    setBusy(`${action}-${id}`);setError("");
    try{const r=await fetch(`${API}/coach/proposals/${id}/${action}`,{method:"POST"});if(!r.ok)throw new Error(await r.text());await load()}catch(e:any){setError(e.message||"Proposal update failed")}finally{setBusy("")}
  }
  async function saveProfile(e:React.FormEvent){
    e.preventDefault();setBusy("profile");setError("");
    try{const r=await fetch(`${API}/coach/profile`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(profile)});if(!r.ok)throw new Error(await r.text());await load()}catch(e:any){setError(e.message||"Profile update failed")}finally{setBusy("")}
  }

  const nextObjective=context?.objectives?.[0];
  const pending=useMemo(()=>proposals.filter(p=>p.status==="pending"),[proposals]);
  return <>
    <div className="row spread coachHeader">
      <div><h1>AI Coach</h1><p className="muted">Grounded analysis and plan changes over deterministic training metrics. Calendar changes require your approval.</p></div>
      <div className="row"><button className="button" disabled={!!busy} onClick={generate}>{busy==="generate"?"Generating…":"Generate next week"}</button><button className="button secondary" disabled={!!busy} onClick={adapt}>{busy==="adapt"?"Analysing…":"Adapt this week"}</button></div>
    </div>

    {context&&<div className="grid section planningMetrics">
      <div className="card"><div className="muted">Fitness</div><div className="metric">{context.state.fitness.toFixed(0)}</div></div>
      <div className="card"><div className="muted">Fatigue</div><div className="metric">{context.state.fatigue.toFixed(0)}</div></div>
      <div className="card"><div className="muted">7-day load</div><div className="metric">{context.state.load_7d.toFixed(0)}</div><small className="muted">Typical {context.state.typical_weekly_load_8w.toFixed(0)}</small></div>
      <div className="card"><div className="muted">Next objective</div><div className="metric metricSmall">{nextObjective?.name||"None"}</div><small className="muted">{nextObjective?`${nextObjective.days_away} days · ${nextObjective.priority}`:"Add one in Season"}</small></div>
    </div>}
    {error&&<div className="card section errorText">{error}</div>}

    <div className="twoCol section">
      <section className="card coachChat"><div className="row spread"><div><h2>Ask Coach</h2><p className="muted">Ask about fatigue, vertical preparation, consistency, progress, or your current training state.</p></div>{context&&<span className="badge">{context.history.activity_count} activities in model</span>}</div>
        <div className="messageStack">
          {messages.length===0&&<div className="emptyCoach">Your training history stays structured. Raw FIT/GPX streams are not sent to the reasoning layer.</div>}
          {messages.map(m=><div className={`coachMessage ${m.role}`} key={m.id}><div className="messageRole">{m.role==="assistant"?"Coach":"You"}</div><div>{m.content}</div>{m.evidence?.length>0&&<div className="evidenceRow">{m.evidence.slice(0,4).map((e,i)=><span className="badge" key={i}>{e.metric}: {String(e.value)}{e.period?` · ${e.period}`:""}</span>)}</div>}</div>)}
        </div>
        <form className="coachAsk" onSubmit={ask}><textarea className="input" rows={3} value={question} onChange={e=>setQuestion(e.target.value)} placeholder="Why is my fatigue high? Am I doing enough vertical? Compare the last four weeks with the previous four."/><button className="button" disabled={busy==="ask"}>{busy==="ask"?"Analysing…":"Ask"}</button></form>
      </section>

      <section className="card"><h2>Planner preferences</h2><p className="muted">These become explicit constraints in the athlete context rather than hidden prompt assumptions.</p><form className="formGrid section" onSubmit={saveProfile}>
        <label>Hours available / week<input className="input" type="number" step="0.5" min="0" value={profile.available_hours_per_week??""} onChange={e=>setProfile({...profile,available_hours_per_week:e.target.value?Number(e.target.value):null})}/></label>
        <label>Long-session day<select className="input" value={profile.preferred_long_day??5} onChange={e=>setProfile({...profile,preferred_long_day:Number(e.target.value)})}>{DAYS.map((d,i)=><option value={i} key={d}>{d}</option>)}</select></label>
        <label>Rest day<select className="input" value={profile.preferred_rest_day??0} onChange={e=>setProfile({...profile,preferred_rest_day:Number(e.target.value)})}>{DAYS.map((d,i)=><option value={i} key={d}>{d}</option>)}</select></label>
        <label className="checkLabel"><input type="checkbox" checked={profile.doubles_allowed} onChange={e=>setProfile({...profile,doubles_allowed:e.target.checked})}/> Doubles allowed</label>
        <button className="button" disabled={busy==="profile"} type="submit">{busy==="profile"?"Saving…":"Save preferences"}</button>
      </form>
      <div className="section coachContext"><h3>Context used</h3><div className="listRow"><span>Current block</span><b>{context?.current_block?.name||"None"}</b></div><div className="listRow"><span>Typical weekly hours</span><b>{context?.state.typical_weekly_hours_8w!=null?formatDuration(context.state.typical_weekly_hours_8w*3600):"-"}</b></div><div className="listRow"><span>28-day adherence</span><b>{context?.adherence_28d_pct==null?"Not enough matched plan data":`${context.adherence_28d_pct.toFixed(0)}%`}</b></div></div>
      </section>
    </div>

    <section className="section"><div className="row spread"><div><h2>Plan proposals</h2><p className="muted">The provider may propose commands, but the deterministic validator checks ownership, locks, availability, dates and projected load before approval.</p></div><span className="badge">{pending.length} pending</span></div>
      <div className="proposalGrid">
        {proposals.length===0&&<div className="card muted">Generate or adapt a week to create the first proposal.</div>}
        {proposals.map(p=><article className={`card proposalCard ${p.status}`} key={p.id}><div className="row spread"><div><span className="sportLabel">{p.proposal_type.replace("_"," ")} · {p.provider}</span><h3>{p.title}</h3></div><span className={`badge ${p.status==="applied"?"success":""}`}>{p.status}</span></div><p>{p.summary}</p>
          <div className="commandStack">{p.commands.map((c,i)=><div className="commandRow" key={i}><strong>{i+1}</strong><div><div>{commandLabel(c)}</div>{c.reason&&<small className="muted">{c.reason}</small>}</div></div>)}{p.commands.length===0&&<div className="muted">No calendar changes proposed.</div>}</div>
          {(p.validation?.warnings||[]).length>0&&<div className="warningStack section">{p.validation.warnings!.map((w,i)=><div className={`warning ${w.severity||""}`} key={i}><strong>Validator</strong><span>{w.message}</span></div>)}</div>}
          {(p.validation?.errors||[]).length>0&&<div className="errorText section">{p.validation.errors!.join(" ")}</div>}
          {p.status==="pending"&&<div className="row section"><button className="button" disabled={!!busy||p.validation?.valid===false||p.commands.length===0} onClick={()=>decide(p.id,"approve")}>Approve changes</button><button className="button secondary" disabled={!!busy} onClick={()=>decide(p.id,"reject")}>Reject</button></div>}
        </article>)}
      </div>
    </section>
  </>
}
