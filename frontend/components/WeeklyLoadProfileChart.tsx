"use client";
import {Bar,BarChart,CartesianGrid,Legend,ResponsiveContainer,Tooltip,XAxis,YAxis} from "recharts";
import ChartTooltip from "./ChartTooltip";
import {formatChartDate} from "@/lib/datetime";

export default function WeeklyLoadProfileChart({data}:{data:any[]}){
  return <div className="chart"><ResponsiveContainer width="100%" height="100%"><BarChart data={data} margin={{top:8,right:16,bottom:4,left:0}}>
    <CartesianGrid strokeDasharray="3 3" opacity={.15}/>
    {/* Bucket keys are the Monday of each week, so label them as a date, not an ISO string. */}
    <XAxis dataKey="week" tickFormatter={(v:string)=>formatChartDate(v)} tickMargin={8} minTickGap={12}/>
    <YAxis tickMargin={6} width={44}/>
    <Tooltip content={<ChartTooltip/>} cursor={{fill:"rgba(152,164,199,.12)"}}/>
    <Legend verticalAlign="top" height={28}/>
    <Bar dataKey="metabolic_load" name="Metabolic" stackId="load" radius={[4,4,0,0]}/>
    <Bar dataKey="ascent_load" name="Ascent" stackId="terrain" radius={[0,0,0,0]}/>
    <Bar dataKey="descent_load" name="Descent" stackId="terrain" radius={[0,0,0,0]}/>
    <Bar dataKey="durability_load" name="Durability" stackId="terrain" radius={[4,4,0,0]}/>
  </BarChart></ResponsiveContainer></div>
}
