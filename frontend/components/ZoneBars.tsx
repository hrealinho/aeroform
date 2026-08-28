"use client";
import {formatDuration} from "@/lib/datetime";

/**
 * Time-in-zone as horizontal bars.
 *
 * Zones are ordinal, not categorical, so they take one hue getting darker rather
 * than a different colour each - and every bar is labelled, so the ordering is
 * never carried by shade alone.
 */
export default function ZoneBars({zones}:{zones:Array<{zone:string;seconds:number;pct:number}>}){
  const max=Math.max(...zones.map(z=>z.pct),1);
  return <div className="zoneBars">
    {zones.map((z,i)=>(
      <div className="zoneBar" key={z.zone}>
        <span className="zoneBarLabel">{z.zone}</span>
        <span className="zoneBarTrack">
          <span className="zoneBarFill" style={{width:`${(z.pct/max)*100}%`,opacity:0.4+0.6*(i/Math.max(zones.length-1,1))}}/>
        </span>
        <span className="zoneBarValue">{formatDuration(z.seconds)}</span>
        <span className="zoneBarPct">{z.pct}%</span>
      </div>
    ))}
  </div>;
}
