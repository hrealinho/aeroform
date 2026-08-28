"use client";
import {Bar,BarChart,CartesianGrid,Legend,ResponsiveContainer,Tooltip,XAxis,YAxis} from "recharts";

export default function WeeklyLoadProfileChart({data}:{data:any[]}){
  return <div className="chart"><ResponsiveContainer width="100%" height="100%"><BarChart data={data}>
    <CartesianGrid strokeDasharray="3 3" opacity={.15}/><XAxis dataKey="week" minTickGap={20}/><YAxis/><Tooltip/><Legend/>
    <Bar dataKey="metabolic_load" name="Metabolic" stackId="load"/><Bar dataKey="ascent_load" name="Ascent" stackId="terrain"/><Bar dataKey="descent_load" name="Descent" stackId="terrain"/><Bar dataKey="durability_load" name="Durability" stackId="terrain"/>
  </BarChart></ResponsiveContainer></div>
}
