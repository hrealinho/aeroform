"use client";
import {CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis} from "recharts";
import ChartTooltip from "./ChartTooltip";
import {formatMonthTick, monthTicks} from "@/lib/datetime";

export default function FitnessChart({data}:{data:any[]}){
  const ticks=monthTicks(data,"date");
  return <div className="chart"><ResponsiveContainer width="100%" height="100%"><LineChart data={data} margin={{top:8,right:16,bottom:4,left:0}}>
    <CartesianGrid strokeDasharray="3 3" opacity={.15}/>
    <XAxis dataKey="date" ticks={ticks} tickFormatter={formatMonthTick} tickMargin={8} minTickGap={16}/>
    <YAxis tickMargin={6} width={44}/>
    <Tooltip content={<ChartTooltip/>} cursor={{strokeDasharray:"3 3"}}/>
    <Legend verticalAlign="top" height={28} iconType="plainline"/>
    <Line type="monotone" dataKey="fitness" name="Fitness" dot={false} strokeWidth={2}/>
    <Line type="monotone" dataKey="fatigue" name="Fatigue" dot={false} strokeWidth={2}/>
    <Line type="monotone" dataKey="form" name="Form" dot={false} strokeWidth={1}/>
  </LineChart></ResponsiveContainer></div>
}
