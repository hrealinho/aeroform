"use client";
import {ResponsiveContainer,LineChart,Line,XAxis,YAxis,Tooltip,CartesianGrid} from "recharts";
export default function FitnessChart({data}:{data:any[]}){return <div className="chart"><ResponsiveContainer width="100%" height="100%"><LineChart data={data}><CartesianGrid strokeDasharray="3 3"/><XAxis dataKey="date" minTickGap={30}/><YAxis/><Tooltip/><Line type="monotone" dataKey="fitness" dot={false}/><Line type="monotone" dataKey="fatigue" dot={false}/><Line type="monotone" dataKey="form" dot={false}/></LineChart></ResponsiveContainer></div>}
