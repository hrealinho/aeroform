"use client";
import {Area, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis} from "recharts";
import ChartLegend from "./ChartLegend";
import {chart, axisProps} from "./chartTheme";
import {formatDuration} from "@/lib/datetime";

type Point={t:number;power?:number;hr?:number;altitude?:number;speed?:number;cadence?:number};

const UNITS:Record<string,string>={power:"W",hr:"bpm",cadence:"rpm",speed:"m/s",altitude:"m"};

function StreamTooltip({active,payload,label}:{active?:boolean;payload?:any[];label?:any}){
  if(!active||!payload?.length) return null;
  return <div className="chartTooltip">
    <div className="chartTooltipDate">{formatDuration(Number(label))} elapsed</div>
    {payload.map((e:any)=><div className="chartTooltipRow" key={e.dataKey}>
      <span className="chartTooltipChip" style={{background:e.color}} aria-hidden/>
      <span className="chartTooltipName">{e.name}</span>
      <span className="chartTooltipValue">{Math.round(e.value)} {UNITS[String(e.dataKey).replace("_max","")]||""}</span>
    </div>)}
  </div>;
}

export default function ActivityStreamChart({points,channels}:{points:Point[];channels:string[]}){
  const hasPower=channels.includes("power");
  const hasHr=channels.includes("hr");
  const hasAlt=channels.includes("altitude");
  if(!points.length) return <p className="muted">No stream stored for this activity.</p>;

  // Altitude is context, not a comparable measure, so it sits on its own hidden axis
  // as a filled band. Two visible value axes would be a dual-axis chart.
  return <>
    <ChartLegend items={[
      ...(hasPower?[{name:"Power",color:chart.series(0)}]:[]),
      ...(hasHr?[{name:"Heart rate",color:chart.fatigue}]:[]),
      ...(hasAlt?[{name:"Elevation",color:chart.series(2)}]:[]),
    ]}/>
    <div className="chart"><ResponsiveContainer width="100%" height="100%"><ComposedChart data={points} margin={{top:6,right:14,bottom:2,left:0}}>
      <defs><linearGradient id="altFill" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stopColor={chart.series(2)} stopOpacity={.22}/><stop offset="100%" stopColor={chart.series(2)} stopOpacity={.02}/>
      </linearGradient></defs>
      <CartesianGrid stroke={chart.grid} strokeDasharray="3 3" vertical={false}/>
      <XAxis dataKey="t" type="number" domain={["dataMin","dataMax"]}
             tickFormatter={(v:number)=>formatDuration(v)} {...axisProps}/>
      <YAxis yAxisId="value" width={44} {...axisProps}/>
      <YAxis yAxisId="alt" hide domain={["dataMin - 40","dataMax + 20"]}/>
      <Tooltip content={<StreamTooltip/>} cursor={{stroke:chart.axis,strokeDasharray:"3 3"}}/>
      {hasAlt&&<Area yAxisId="alt" type="monotone" dataKey="altitude" name="Elevation"
             stroke={chart.series(2)} strokeWidth={1} fill="url(#altFill)" dot={false}/>}
      {hasPower&&<Line yAxisId="value" type="monotone" dataKey="power" name="Power"
             stroke={chart.series(0)} strokeWidth={1.4} dot={false}/>}
      {hasHr&&<Line yAxisId="value" type="monotone" dataKey="hr" name="Heart rate"
             stroke={chart.fatigue} strokeWidth={1.4} dot={false}/>}
    </ComposedChart></ResponsiveContainer></div>
  </>;
}
