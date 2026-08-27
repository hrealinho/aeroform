import {getJSON} from "@/lib/api";
export default async function Page(){
  let activities:any[]=[];
  try{activities=await getJSON<any[]>("/activities?limit=200")}catch{}
  return <><h1>Activities</h1><p className="muted">Canonical activities with transparent load calculations and stream-derived metrics.</p><div className="card section"><table className="table"><thead><tr><th>Date</th><th>Sport</th><th>Duration</th><th>Distance</th><th>Load</th><th>NP</th><th>Decoupling</th><th>Mechanical</th><th>Method</th></tr></thead><tbody>{activities.map(a=><tr key={a.id}><td>{new Date(a.start_time).toLocaleDateString()}</td><td><span className="badge">{a.sport}</span></td><td>{(a.duration_s/3600).toFixed(1)} h</td><td>{a.distance_m?(a.distance_m/1000).toFixed(1)+" km":"-"}</td><td>{a.training_load?.toFixed?.(0)??"-"}</td><td>{a.normalized_power?Math.round(a.normalized_power)+" W":"-"}</td><td>{a.metric_details?.aerobic_decoupling_pct!=null?`${a.metric_details.aerobic_decoupling_pct}%`:"-"}</td><td>{a.mechanical_load?.toFixed?.(0)??"-"}</td><td>{a.load_method||"-"} / {a.load_confidence||"-"}</td></tr>)}</tbody></table></div></>;
}
