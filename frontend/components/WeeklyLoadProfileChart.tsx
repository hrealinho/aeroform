"use client";
import {Bar,BarChart,CartesianGrid,ResponsiveContainer,Tooltip,XAxis,YAxis} from "recharts";
import ChartLegend from "./ChartLegend";
import ChartTooltip from "./ChartTooltip";
import {chart, axisProps} from "./chartTheme";
import {formatChartDate} from "@/lib/datetime";

// Metabolic is its own bar; the terrain components stack beside it. Adjacent slots
// were validated as a set, which is the pairlist that applies to stacked bars.
const TERRAIN=[
  {key:"ascent_load",name:"Ascent",slot:1},
  {key:"descent_load",name:"Descent",slot:2},
  {key:"durability_load",name:"Durability",slot:3},
];

export default function WeeklyLoadProfileChart({data}:{data:any[]}){
  return <>
    <ChartLegend items={[{name:"Metabolic",color:chart.series(0)},...TERRAIN.map(t=>({name:t.name,color:chart.series(t.slot)}))]}/>
    <div className="chart"><ResponsiveContainer width="100%" height="100%"><BarChart data={data} margin={{top:6,right:14,bottom:2,left:0}} barGap={2}>
      <CartesianGrid stroke={chart.grid} strokeDasharray="3 3" vertical={false}/>
      {/* Bucket keys are the Monday of each week, so they read as a date. */}
      <XAxis dataKey="week" tickFormatter={(v:string)=>formatChartDate(v)} {...axisProps} minTickGap={12}/>
      <YAxis width={42} {...axisProps}/>
      <Tooltip content={<ChartTooltip/>} cursor={{fill:"rgba(152,164,199,.10)"}}/>
      <Bar dataKey="metabolic_load" name="Metabolic" stackId="metabolic" fill={chart.series(0)} radius={[3,3,0,0]} maxBarSize={26}/>
      {TERRAIN.map((t,i)=>(
        <Bar key={t.key} dataKey={t.key} name={t.name} stackId="terrain" fill={chart.series(t.slot)}
             radius={i===TERRAIN.length-1?[3,3,0,0]:[0,0,0,0]} maxBarSize={26}
             /* 2px surface gap so stacked segments read as separate marks. */
             stroke={chart.surface} strokeWidth={2}/>
      ))}
    </BarChart></ResponsiveContainer></div>
  </>;
}
