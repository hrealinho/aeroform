"use client";
import {useEffect,useState} from "react";
import {API} from "@/lib/api";

type Status={connected:boolean;configured:boolean;status?:string;external_athlete_id?:string;last_sync_at?:string;last_error?:string;latest_import?:{id:number;status:string;discovered_count:number;imported_count:number;duplicate_count:number;failed_count:number}};

export default function StravaConnection(){
  const [data,setData]=useState<Status|null>(null);
  const [message,setMessage]=useState("");

  async function refresh(){
    try{const r=await fetch(API+"/strava/status",{cache:"no-store"});setData(await r.json());}catch{setMessage("Could not reach API");}
  }
  useEffect(()=>{void refresh();const timer=setInterval(()=>void refresh(),4000);return()=>clearInterval(timer)},[]);

  async function connect(){
    setMessage("Opening Strava...");
    const r=await fetch(API+"/strava/connect");
    const j=await r.json();
    if(!r.ok){setMessage(j.detail||"Strava is not configured");return;}
    window.location.href=j.authorization_url;
  }
  async function sync(){
    setMessage("Starting historical sync...");
    const r=await fetch(API+"/strava/sync",{method:"POST"});
    const j=await r.json();
    setMessage(r.ok?`Sync started (import #${j.import_session_id})`:j.detail||"Sync failed");
    void refresh();
  }

  return <div className="card section">
    <div className="row spread"><div><h2>Strava</h2><p className="muted">OAuth connection, historical backfill, token refresh and webhook-ready ongoing sync.</p></div><span className="badge">{data?.connected?data.status||"connected":"not connected"}</span></div>
    {!data?.configured&&<p className="muted">Set STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET on the API and worker first.</p>}
    <div className="row">
      {!data?.connected?<button className="button" onClick={connect} disabled={!data?.configured}>Connect Strava</button>:<button className="button" onClick={sync}>Sync history</button>}
      {data?.last_sync_at&&<span className="muted">Last sync {new Date(data.last_sync_at).toLocaleString()}</span>}
    </div>
    {data?.latest_import&&<div className="connectionStats">
      <span>{data.latest_import.imported_count} imported</span><span>{data.latest_import.duplicate_count} duplicates</span><span>{data.latest_import.failed_count} failed</span><span>{data.latest_import.status}</span>
    </div>}
    {data?.last_error&&<p className="errorText">{data.last_error}</p>}{message&&<p>{message}</p>}
  </div>;
}
