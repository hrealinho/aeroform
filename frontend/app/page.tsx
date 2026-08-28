import FitnessChart from "@/components/FitnessChart";
import WeeklyLoadProfileChart from "@/components/WeeklyLoadProfileChart";
import LoadExplorer from "@/components/LoadExplorer";
import {getJSON} from "@/lib/api";

export default async function Page(){
  let data:any[]=[];let weekly:any[]=[];
  try{[data,weekly]=await Promise.all([getJSON<any[]>("/analytics/fitness"),getJSON<any[]>("/analytics/weekly?weeks=12")])}catch{}
  const latest=data.at(-1)||{fitness:0,fatigue:0,form:0};
  const load7=data.slice(-7).reduce((a,b)=>a+b.load,0);const load28=data.slice(-28).reduce((a,b)=>a+b.load,0);
  const recent=weekly.at(-1)||{};
  return <><h1>Training dashboard</h1><p className="muted">Current state plus sport-aware metabolic and terrain load.</p>
    <div className="grid section"><div className="card"><div className="muted">Fitness</div><div className="metric">{latest.fitness}</div></div><div className="card"><div className="muted">Fatigue</div><div className="metric">{latest.fatigue}</div></div><div className="card"><div className="muted">Form</div><div className="metric">{latest.form}</div></div><div className="card"><div className="muted">7-day load</div><div className="metric">{load7.toFixed(0)}</div></div></div>
    <div className="card section"><div className="row"><div><h2>Fitness, fatigue and form</h2><div className="muted">28-day composite load {load28.toFixed(0)}</div></div></div><FitnessChart data={data}/></div>
    <div className="card section"><div className="row spread"><div><h2>Weekly load profile</h2><div className="muted">Metabolic stress is shown separately from ascent, descent and durability load.</div></div><div className="muted">Latest: ↑{recent.elevation_gain_m||0} m / ↓{recent.elevation_loss_m||0} m</div></div><WeeklyLoadProfileChart data={weekly}/></div>
    <div className="card section"><LoadExplorer/></div>
  </>;
}
