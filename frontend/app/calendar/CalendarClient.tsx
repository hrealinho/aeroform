"use client";

import {useCallback, useEffect, useMemo, useState} from "react";
import {API} from "@/lib/api";
import Icon from "@/components/Icon";
import {formatDuration, formatDurationHours, localDateTimeToUTC, localISODate, localISODateOf} from "@/lib/datetime";

const SPORTS = ["running", "trail_running", "cycling", "hiking", "mountaineering", "climbing"];
const INTENSITIES = ["recovery", "easy", "endurance", "steady", "tempo", "threshold", "vo2", "anaerobic", "race"];

type Planned = {id:number; scheduled_at:string; sport:string; name:string; duration_s?:number; distance_m?:number; elevation_m?:number; projected_load?:number; intensity:string; locked:boolean; matched_activity_id?:number|null};
type Activity = {id:number; start_time:string; sport:string; name?:string; duration_s:number; distance_m?:number; elevation_gain_m?:number; training_load:number; matched_workout_id?:number|null};
type Objective = {id:number; name:string; event_date:string; priority:string};
type Block = {id:number; name:string; block_type:string; start_date:string; end_date:string};
type CalendarData = {planned:Planned[]; activities:Activity[]; objectives:Objective[]; blocks:Block[]};

type Projection = {warnings:{code:string;severity:string;message:string}[]; weeks:{week:string;load:number;planned_load:number;actual_load:number}[]};

const isoDate=localISODate;
function mondayFor(d:Date){const x=new Date(d);const day=(x.getDay()+6)%7;x.setDate(x.getDate()-day);x.setHours(12,0,0,0);return x}
// Durations read as clock time, not as a decimal fraction of an hour.
const hours=formatDuration;
function km(m?:number){return m ? `${Math.round(m/100)/10} km` : ""}
function dayLabel(d:Date){return d.toLocaleDateString(undefined,{weekday:"short",day:"numeric",month:"short"})}

// Planning happens in blocks, not single weeks, so the calendar has to show one.
const RANGES=[{weeks:1,label:"Week"},{weeks:4,label:"4 weeks"},{weeks:8,label:"8 weeks"},{weeks:13,label:"3 months"}];

export default function CalendarClient(){
  const [week,setWeek]=useState(()=>mondayFor(new Date()));
  const [weeksShown,setWeeksShown]=useState(4);
  const [data,setData]=useState<CalendarData>({planned:[],activities:[],objectives:[],blocks:[]});
  const [projection,setProjection]=useState<Projection>({warnings:[],weeks:[]});
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState("");
  const [why,setWhy]=useState<{title:string;reasons:string[];evidence:any[]}|null>(null);
  const [form,setForm]=useState({date:isoDate(new Date()),time:"18:00",sport:"running",name:"Easy run",durationMin:"60",distanceKm:"",elevationM:"",intensity:"easy",warmupMin:"",reps:"",workMin:"",recoveryMin:"",cooldownMin:""});
  const days=useMemo(()=>Array.from({length:7*weeksShown},(_,i)=>{const d=new Date(week);d.setDate(d.getDate()+i);return d}),[week,weeksShown]);
  const weekRows=useMemo(()=>Array.from({length:weeksShown},(_,w)=>days.slice(w*7,w*7+7)),[days,weeksShown]);
  const start=isoDate(days[0]); const end=isoDate(days[days.length-1]);
  const fetchStart=useMemo(()=>{const d=new Date(days[0]);d.setDate(d.getDate()-1);return isoDate(d)},[days]);
  const fetchEnd=useMemo(()=>{const d=new Date(days[days.length-1]);d.setDate(d.getDate()+1);return isoDate(d)},[days]);

  const load=useCallback(async()=>{
    setLoading(true);setError("");
    try{
      const [cal,proj]=await Promise.all([
        fetch(`${API}/calendar?start=${fetchStart}&end=${fetchEnd}`).then(r=>r.ok?r.json():Promise.reject(new Error("Calendar request failed"))),
        fetch(`${API}/analytics/projection?start=${fetchStart}&end=${fetchEnd}`).then(r=>r.ok?r.json():Promise.reject(new Error("Projection request failed")))
      ]);
      setData(cal);setProjection(proj);
    }catch(e:any){setError(e.message||"Failed to load calendar")}finally{setLoading(false)}
  },[fetchStart,fetchEnd]);
  useEffect(()=>{load()},[load]);

  async function createWorkout(e:React.FormEvent){
    e.preventDefault();
    const reps=Number(form.reps||0);
    const structured=reps>0&&Number(form.workMin||0)>0;
    const steps:any[]=[];
    if(structured){
      if(Number(form.warmupMin||0)>0)steps.push({type:"warmup",intensity:"easy",duration_s:Number(form.warmupMin)*60});
      steps.push({type:"repeat",repeat:reps,steps:[{type:"work",intensity:form.intensity,duration_s:Number(form.workMin)*60},{type:"recovery",intensity:"easy",duration_s:Number(form.recoveryMin||0)*60}]});
      if(Number(form.cooldownMin||0)>0)steps.push({type:"cooldown",intensity:"easy",duration_s:Number(form.cooldownMin)*60});
    }
    const structuredDuration=structured?(Number(form.warmupMin||0)+reps*(Number(form.workMin||0)+Number(form.recoveryMin||0))+Number(form.cooldownMin||0)):Number(form.durationMin);
    const payload={scheduled_at:localDateTimeToUTC(form.date,form.time),sport:form.sport,name:form.name,duration_s:structuredDuration*60,distance_m:form.distanceKm?Number(form.distanceKm)*1000:null,elevation_m:form.elevationM?Number(form.elevationM):null,intensity:form.intensity,steps};
    const r=await fetch(`${API}/planned-workouts`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    if(!r.ok){setError(await r.text());return} await load();
  }
  async function moveWorkout(id:number,target:Date){
    const w=data.planned.find(x=>x.id===id); if(!w||w.locked) return;
    const old=new Date(w.scheduled_at); const next=new Date(target);next.setHours(old.getHours(),old.getMinutes(),0,0);
    const r=await fetch(`${API}/planned-workouts/${id}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({scheduled_at:next.toISOString()})});
    if(!r.ok){setError(await r.text());return} await load();
  }
  async function toggleLock(w:Planned){
    await fetch(`${API}/planned-workouts/${w.id}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({locked:!w.locked})});await load();
  }
  async function remove(w:Planned){
    const r=await fetch(`${API}/planned-workouts/${w.id}`,{method:"DELETE"});if(!r.ok){setError(await r.text());return}await load();
  }
  async function whyWorkout(w:Planned){
    const r=await fetch(`${API}/planned-workouts/${w.id}/why`);
    if(!r.ok){setError(await r.text());return}
    setWhy(await r.json());
  }
  async function autoMatch(){
    const r=await fetch(`${API}/matching/auto?start=${start}&end=${end}`,{method:"POST"});if(!r.ok){setError(await r.text());return}await load();
  }
  function changeWeek(delta:number){const d=new Date(week);d.setDate(d.getDate()+delta*7*weeksShown);setWeek(d)}

  const weekLoad=data.planned.reduce((s,w)=>s+(w.projected_load||0),0);
  const actualLoad=data.activities.reduce((s,a)=>s+(a.training_load||0),0);
  return <>
    <div className="row spread calendarHeader">
      <div><h1>Calendar</h1><p className="muted">Plan the week, compare actual training, and see load consequences before you move sessions.</p></div>
      <div className="row">
        {RANGES.map(r=><button key={r.weeks} className={weeksShown===r.weeks?"button":"button secondary"} onClick={()=>setWeeksShown(r.weeks)}>{r.label}</button>)}
        <span className="calendarNavGap"/>
        <button className="button secondary" onClick={()=>changeWeek(-1)}>Previous</button>
        <button className="button secondary" onClick={()=>setWeek(mondayFor(new Date()))}>Today</button>
        <button className="button secondary" onClick={()=>changeWeek(1)}>Next</button>
      </div>
    </div>

    <div className="grid section planningMetrics">
      <div className="card"><div className="muted">Planned load</div><div className="metric">{weekLoad.toFixed(0)}</div></div>
      <div className="card"><div className="muted">Actual load</div><div className="metric">{actualLoad.toFixed(0)}</div></div>
      <div className="card"><div className="muted">Planned sessions</div><div className="metric">{data.planned.length}</div></div>
      <div className="card"><div className="muted">Matched</div><div className="metric">{data.planned.filter(w=>w.matched_activity_id).length}</div></div>
    </div>

    {projection.warnings.length>0 && <div className="section warningStack">{projection.warnings.map((w,i)=><div className={`warning ${w.severity}`} key={i}><strong>Plan warning</strong><span>{w.message}</span></div>)}</div>}
    {error && <div className="card section errorText">{error}</div>}

    <div className="section calendarRange">
      <div className="muted">{new Date(days[0]).toLocaleDateString(undefined,{day:"numeric",month:"short"})} - {new Date(days[days.length-1]).toLocaleDateString(undefined,{day:"numeric",month:"short",year:"numeric"})}</div>
    </div>
    {weekRows.map((rowDays,rowIndex)=>{
      const rowStart=isoDate(rowDays[0]), rowEnd=isoDate(rowDays[6]);
      const rowPlanned=data.planned.filter(w=>{const d=localISODateOf(w.scheduled_at);return d>=rowStart&&d<=rowEnd});
      const rowActual=data.activities.filter(a=>{const d=localISODateOf(a.start_time);return d>=rowStart&&d<=rowEnd});
      const plannedLoad=rowPlanned.reduce((s,w)=>s+(w.projected_load||0),0);
      const actualLoad=rowActual.reduce((s,a)=>s+(a.training_load||0),0);
      const plannedHours=rowPlanned.reduce((s,w)=>s+(w.duration_s||0),0);
      return <section className="weekRow section" key={rowStart}>
        <header className="weekRowHeader">
          <div className="row">
            <b>{new Date(rowDays[0]).toLocaleDateString(undefined,{day:"numeric",month:"short"})}</b>
            <span className="muted">week {rowIndex+1} of {weeksShown}</span>
          </div>
          <div className="row">
            <span className="badge">planned {plannedLoad.toFixed(0)}</span>
            {actualLoad>0&&<span className="badge success">actual {actualLoad.toFixed(0)}</span>}
            {plannedHours>0&&<span className="badge">{hours(plannedHours)}</span>}
            <span className="badge">{rowPlanned.length} sessions</span>
          </div>
        </header>
        <div className="weekGrid">
      {rowDays.map(day=>{
        const ds=isoDate(day);
        const planned=data.planned.filter(w=>localISODateOf(w.scheduled_at)===ds);
        const actual=data.activities.filter(a=>localISODateOf(a.start_time)===ds);
        const objective=data.objectives.filter(o=>o.event_date===ds);
        const block=data.blocks.find(b=>b.start_date<=ds&&b.end_date>=ds);
        return <section className="dayColumn" key={ds} onDragOver={e=>e.preventDefault()} onDrop={e=>{const id=Number(e.dataTransfer.getData("workoutId"));if(id)moveWorkout(id,day)}}>
          <header className="dayHeader"><strong>{dayLabel(day)}</strong>{block&&<span className="blockPill">{block.name}</span>}</header>
          {objective.map(o=><div className="objectiveCard" key={o.id}><b>{o.priority}</b> {o.name}</div>)}
          <div className="daySessions">
            {planned.map(w=><article draggable={!w.locked} onDragStart={e=>e.dataTransfer.setData("workoutId",String(w.id))} className={`workoutCard ${w.matched_activity_id?"matched":""}`} key={w.id}>
              <div className="row spread"><span className="sportLabel">{w.sport.replace("_"," ")}</span><span>{w.locked?<Icon name="lock" size={12}/>:null}</span></div>
              <strong>{w.name}</strong>
              <div className="workoutMeta"><span>{hours(w.duration_s)}</span><span>{km(w.distance_m)}</span><span>Load {Math.round(w.projected_load||0)}</span></div>
              <div className="row"><span className="badge">{w.intensity}</span>{w.matched_activity_id&&<span className="badge success">matched</span>}</div>
              <div className="cardActions"><button onClick={()=>whyWorkout(w)}>Why?</button><button onClick={()=>toggleLock(w)}>{w.locked?"Unlock":"Lock"}</button><button disabled={w.locked} onClick={()=>remove(w)}>Delete</button></div>
            </article>)}
            {actual.map(a=><article className="actualCard" key={a.id}><div className="row spread"><span className="sportLabel">ACTUAL · {a.sport.replace("_"," ")}</span><span className="badge success">{Math.round(a.training_load||0)} load</span></div><strong>{a.name||a.sport}</strong><div className="workoutMeta"><span>{hours(a.duration_s)}</span><span>{km(a.distance_m)}</span>{a.elevation_gain_m?<span>{Math.round(a.elevation_gain_m)}m+</span>:null}</div></article>)}
            {!loading&&planned.length===0&&actual.length===0&&<div className="emptyDay">Drop workout here</div>}
          </div>
        </section>
      })}
        </div>
      </section>;
    })}

    <div className="twoCol section">
      <form className="card" onSubmit={createWorkout}><h2>Add planned workout</h2><div className="formGrid">
        <label>Date<input className="input" type="date" value={form.date} onChange={e=>setForm({...form,date:e.target.value})}/></label>
        <label>Time<input className="input" type="time" value={form.time} onChange={e=>setForm({...form,time:e.target.value})}/></label>
        <label>Sport<select className="input" value={form.sport} onChange={e=>setForm({...form,sport:e.target.value})}>{SPORTS.map(s=><option key={s}>{s}</option>)}</select></label>
        <label>Intensity<select className="input" value={form.intensity} onChange={e=>setForm({...form,intensity:e.target.value})}>{INTENSITIES.map(s=><option key={s}>{s}</option>)}</select></label>
        <label className="span2">Name<input className="input" value={form.name} onChange={e=>setForm({...form,name:e.target.value})}/></label>
        <label>Duration (min)<input className="input" type="number" min="1" value={form.durationMin} onChange={e=>setForm({...form,durationMin:e.target.value})}/></label>
        <label>Distance (km)<input className="input" type="number" step="0.1" value={form.distanceKm} onChange={e=>setForm({...form,distanceKm:e.target.value})}/></label>
        <label>Elevation (m)<input className="input" type="number" value={form.elevationM} onChange={e=>setForm({...form,elevationM:e.target.value})}/></label>
      </div><h3 className="section">Optional interval structure</h3><p className="muted">Set repeats to build a structured workout. When present, the structured steps determine total duration and remain available to the future AI/device-export layer.</p><div className="formGrid">
        <label>Warm-up (min)<input className="input" type="number" min="0" value={form.warmupMin} onChange={e=>setForm({...form,warmupMin:e.target.value})}/></label>
        <label>Repeats<input className="input" type="number" min="0" value={form.reps} onChange={e=>setForm({...form,reps:e.target.value})}/></label>
        <label>Work (min)<input className="input" type="number" min="0" step="0.5" value={form.workMin} onChange={e=>setForm({...form,workMin:e.target.value})}/></label>
        <label>Recovery (min)<input className="input" type="number" min="0" step="0.5" value={form.recoveryMin} onChange={e=>setForm({...form,recoveryMin:e.target.value})}/></label>
        <label>Cool-down (min)<input className="input" type="number" min="0" value={form.cooldownMin} onChange={e=>setForm({...form,cooldownMin:e.target.value})}/></label>
      </div><button className="button section" type="submit">Add workout</button></form>
      <div className="card"><h2>Plan vs actual</h2><p className="muted">Automatically match imported activities to planned sessions using sport, time, duration and distance. Matches never duplicate activity load.</p><button className="button" onClick={autoMatch}>Match completed activities</button><div className="section"><h3>Projection rule checks</h3><p className="muted">v0.4 validates weekly load jumps, key-session spacing, locks and availability before AI proposals can be applied.</p></div>{why&&<div className="section whyPanel"><div className="row spread"><h3>{why.title}</h3><button className="button secondary" onClick={()=>setWhy(null)}>Close</button></div><ul>{why.reasons.map((r,i)=><li key={i}>{r}</li>)}</ul>{why.evidence.length>0&&<div className="evidenceRow">{why.evidence.map((e:any,i:number)=><span className="badge" key={i}>{e.date} · {e.duration_s!=null?formatDuration(e.duration_s):formatDurationHours(e.duration_h)} · {e.load??"?"} load</span>)}</div>}</div>}</div>
    </div>
  </>
}
