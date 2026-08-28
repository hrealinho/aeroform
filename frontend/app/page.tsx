import FitnessChart from "@/components/FitnessChart";
import WeeklyLoadProfileChart from "@/components/WeeklyLoadProfileChart";
import LoadExplorer from "@/components/LoadExplorer";
import {getJSON} from "@/lib/api";
import {formatDuration} from "@/lib/datetime";

type Row={date:string;load:number;fitness:number;fatigue:number;form:number};
type Week={week:string;load:number;hours:number;distance_km:number;elevation_gain_m:number;elevation_loss_m:number;activities:number};

function Tile({label,value,sub}:{label:string;value:string;sub?:string}){
  return <div className="card">
    <div className="eyebrow">{label}</div>
    <div className="metric">{value}</div>
    {sub&&<div className="muted">{sub}</div>}
  </div>;
}

export default async function Page(){
  let data:Row[]=[];let weekly:Week[]=[];let error="";
  try{
    [data,weekly]=await Promise.all([getJSON<Row[]>("/analytics/fitness"),getJSON<Week[]>("/analytics/weekly?weeks=12")]);
  }catch(e:any){
    error=e?.message||"Could not reach the API";
  }

  const latest=data.at(-1)||{fitness:0,fatigue:0,form:0} as Row;
  const load7=data.slice(-7).reduce((a,b)=>a+(b.load||0),0);
  const load28=data.slice(-28).reduce((a,b)=>a+(b.load||0),0);
  const week=weekly.at(-1);
  const prior=weekly.at(-2);
  const trend=week&&prior&&prior.load>0?Math.round((week.load/prior.load-1)*100):null;

  return <>
    <div className="row spread">
      <div>
        <h1>Dashboard</h1>
        <p className="muted">Sport-aware load, with metabolic and terrain stress kept separate.</p>
      </div>
      {data.length>0&&<span className="badge">{data.length} days modelled</span>}
    </div>

    {error&&<div className="card section">
      <div className="warning high"><strong>API unreachable</strong><span>{error}</span></div>
      <p className="muted section">Check the API is running and that NEXT_PUBLIC_API_URL / API_URL_INTERNAL point at it.</p>
    </div>}

    <div className="grid section planningMetrics">
      <Tile label="Fitness" value={String(Math.round(latest.fitness))} sub="42-day load average"/>
      <Tile label="Fatigue" value={String(Math.round(latest.fatigue))} sub="7-day load average"/>
      <Tile label="Form" value={`${latest.form>0?"+":""}${Math.round(latest.form)}`} sub="fitness minus fatigue"/>
      <Tile label="Load 7d" value={load7.toFixed(0)} sub={`28d ${load28.toFixed(0)}`}/>
    </div>

    <div className="card section">
      <div className="row spread">
        <div><h2>Fitness, fatigue and form</h2><div className="muted">Composite load, exponentially weighted.</div></div>
      </div>
      <FitnessChart data={data}/>
    </div>

    <div className="card section">
      <div className="row spread">
        <div><h2>Weekly load profile</h2><div className="muted">Metabolic stress shown apart from ascent, descent and time on feet.</div></div>
        {week&&<div className="row">
          <span className="badge">{formatDuration(week.hours*3600)}</span>
          <span className="badge">{Math.round(week.distance_km)} km</span>
          <span className="badge">↑{Math.round(week.elevation_gain_m)} m</span>
          {trend!==null&&<span className="badge">{trend>=0?"+":""}{trend}% vs prior week</span>}
        </div>}
      </div>
      <WeeklyLoadProfileChart data={weekly}/>
    </div>

    <div className="card section"><LoadExplorer/></div>
  </>;
}
