"use client";
import {CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis} from "recharts";
export default function ProjectionChart({data}:{data:any[]}){
  const today=new Date().toISOString().slice(0,10);
  return <div className="chart"><ResponsiveContainer width="100%" height="100%"><LineChart data={data}><CartesianGrid strokeDasharray="3 3" opacity={.15}/><XAxis dataKey="date" minTickGap={28}/><YAxis/><Tooltip/><ReferenceLine x={today} label="Today" strokeDasharray="4 4"/><Line type="monotone" dataKey="fitness" dot={false} strokeWidth={2}/><Line type="monotone" dataKey="fatigue" dot={false} strokeWidth={2}/><Line type="monotone" dataKey="form" dot={false} strokeWidth={1}/></LineChart></ResponsiveContainer></div>
}
