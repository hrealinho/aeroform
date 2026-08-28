"use client";
import {CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis} from "recharts";
import ChartTooltip from "./ChartTooltip";
import {formatMonthTick, localISODate, monthTicks} from "@/lib/datetime";

export default function ProjectionChart({data}:{data:any[]}){
  const today=localISODate(new Date());
  // Ticks on month boundaries rather than wherever the pixel gap happens to fall.
  const ticks=monthTicks(data,"date");
  return <div className="chart"><ResponsiveContainer width="100%" height="100%"><LineChart data={data} margin={{top:8,right:16,bottom:4,left:0}}>
    <CartesianGrid strokeDasharray="3 3" opacity={.15}/>
    <XAxis dataKey="date" ticks={ticks} tickFormatter={formatMonthTick} tickMargin={8} minTickGap={16}/>
    <YAxis tickMargin={6} width={44}/>
    <Tooltip content={<ChartTooltip/>} cursor={{strokeDasharray:"3 3"}}/>
    <Legend verticalAlign="top" height={28} iconType="plainline"/>
    <ReferenceLine x={today} stroke="#98a4c7" strokeDasharray="4 4" label={{value:"Today",position:"insideTopRight",fill:"#98a4c7",fontSize:12}}/>
    <Line type="monotone" dataKey="fitness" name="Fitness" dot={false} strokeWidth={2}/>
    <Line type="monotone" dataKey="fatigue" name="Fatigue" dot={false} strokeWidth={2}/>
    <Line type="monotone" dataKey="form" name="Form" dot={false} strokeWidth={1}/>
  </LineChart></ResponsiveContainer></div>
}
