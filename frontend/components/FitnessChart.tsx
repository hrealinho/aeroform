"use client";
import {Area, AreaChart, CartesianGrid, Line, ResponsiveContainer, Tooltip, XAxis, YAxis} from "recharts";
import ChartLegend from "./ChartLegend";
import ChartTooltip from "./ChartTooltip";
import {chart, axisProps} from "./chartTheme";
import {formatMonthTick, monthTicks} from "@/lib/datetime";

export default function FitnessChart({data}:{data:any[]}){
  const ticks=monthTicks(data,"date");
  return <>
    <ChartLegend items={[{name:"Fitness",color:chart.fitness},{name:"Fatigue",color:chart.fatigue},{name:"Form",color:chart.form}]}/>
    <div className="chart"><ResponsiveContainer width="100%" height="100%"><AreaChart data={data} margin={{top:6,right:14,bottom:2,left:0}}>
      <defs><linearGradient id="fitnessFill" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stopColor={chart.fitness} stopOpacity={.28}/><stop offset="100%" stopColor={chart.fitness} stopOpacity={0}/>
      </linearGradient></defs>
      <CartesianGrid stroke={chart.grid} strokeDasharray="3 3" vertical={false}/>
      <XAxis dataKey="date" ticks={ticks} tickFormatter={formatMonthTick} {...axisProps}/>
      <YAxis width={42} {...axisProps}/>
      <Tooltip content={<ChartTooltip/>} cursor={{stroke:chart.axis,strokeDasharray:"3 3"}}/>
      {/* Fitness is the slow-moving baseline, so it carries the fill. */}
      <Area type="monotone" dataKey="fitness" name="Fitness" stroke={chart.fitness} strokeWidth={2} fill="url(#fitnessFill)" dot={false} activeDot={{r:4,strokeWidth:0}}/>
      <Line type="monotone" dataKey="fatigue" name="Fatigue" stroke={chart.fatigue} strokeWidth={2} dot={false} activeDot={{r:4,strokeWidth:0}}/>
      <Line type="monotone" dataKey="form" name="Form" stroke={chart.form} strokeWidth={1.5} strokeDasharray="4 3" dot={false} activeDot={{r:4,strokeWidth:0}}/>
    </AreaChart></ResponsiveContainer></div>
  </>;
}
