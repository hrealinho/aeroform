"use client";
import {CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis} from "recharts";
import ChartLegend from "./ChartLegend";
import {chart, axisProps} from "./chartTheme";
import {formatDuration} from "@/lib/datetime";

type Point={duration_s:number;watts:number;w_per_kg:number|null;previous_watts:number|null;date:string|null};

/** Duration ticks a rider actually thinks in, rather than even pixel spacing. */
const TICKS=[5,30,60,300,1200,3600,10800];

function CurveTooltip({active,payload,label}:{active?:boolean;payload?:any[];label?:any}){
  if(!active||!payload?.length) return null;
  const row=payload[0]?.payload as Point|undefined;
  return <div className="chartTooltip">
    <div className="chartTooltipDate">{formatDuration(Number(label))}</div>
    {payload.map((e:any)=><div className="chartTooltipRow" key={e.dataKey}>
      <span className="chartTooltipChip" style={{background:e.color}} aria-hidden/>
      <span className="chartTooltipName">{e.name}</span>
      <span className="chartTooltipValue">{Math.round(e.value)} W</span>
    </div>)}
    {row?.w_per_kg!=null&&<div className="chartTooltipRow"><span className="chartTooltipName">W/kg</span><span className="chartTooltipValue">{row.w_per_kg}</span></div>}
    {row?.date&&<div className="muted" style={{marginTop:6}}>set {row.date}</div>}
  </div>;
}

export default function PowerCurveChart({points,compareLabel}:{points:Point[];compareLabel?:string}){
  const hasCompare=points.some(p=>p.previous_watts!=null);
  return <>
    <ChartLegend items={[
      {name:"Best",color:chart.fitness},
      ...(hasCompare?[{name:compareLabel||"Previous period",color:chart.reference,dashed:true}]:[]),
    ]}/>
    <div className="chart"><ResponsiveContainer width="100%" height="100%"><LineChart data={points} margin={{top:6,right:14,bottom:2,left:0}}>
      <CartesianGrid stroke={chart.grid} strokeDasharray="3 3"/>
      {/* Log scale: a power curve is read across orders of magnitude of time. */}
      <XAxis dataKey="duration_s" type="number" scale="log" domain={["dataMin","dataMax"]}
             ticks={TICKS} tickFormatter={(v:number)=>formatDuration(v)} {...axisProps}/>
      <YAxis width={48} unit=" W" {...axisProps}/>
      <Tooltip content={<CurveTooltip/>} cursor={{stroke:chart.axis,strokeDasharray:"3 3"}}/>
      {hasCompare&&<Line type="monotone" dataKey="previous_watts" name={compareLabel||"Previous period"}
             stroke={chart.reference} strokeWidth={1.5} strokeDasharray="4 3" dot={false} connectNulls/>}
      <Line type="monotone" dataKey="watts" name="Best" stroke={chart.fitness} strokeWidth={2.5}
            dot={{r:3,strokeWidth:0,fill:chart.fitness}} activeDot={{r:5,strokeWidth:0}}/>
    </LineChart></ResponsiveContainer></div>
  </>;
}
