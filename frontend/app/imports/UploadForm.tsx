"use client";
import {useState} from "react";
import {API} from "@/lib/api";

type ImportStatus={id:number;status:string;discovered_count:number;imported_count:number;duplicate_count:number;failed_count:number};

export default function UploadForm(){
  const [status,setStatus]=useState("");
  const [progress,setProgress]=useState<ImportStatus|null>(null);

  async function poll(id:number){
    for(let i=0;i<720;i++){
      const r=await fetch(`${API}/imports/${id}`,{cache:"no-store"});
      if(!r.ok) break;
      const j:ImportStatus=await r.json();
      setProgress(j);
      if(["completed","completed_with_errors","failed"].includes(j.status)){
        setStatus(j.status==="completed"?"Import complete":"Import finished with issues");
        return;
      }
      await new Promise(resolve=>setTimeout(resolve,1500));
    }
  }

  async function submit(e:React.FormEvent<HTMLFormElement>){
    e.preventDefault();
    const fd=new FormData(e.currentTarget);
    setStatus("Uploading...");
    setProgress(null);
    const r=await fetch(API+"/imports/files",{method:"POST",body:fd});
    const j=await r.json();
    if(!r.ok){setStatus(`Failed: ${j.detail||JSON.stringify(j)}`);return;}
    setProgress(j);
    setStatus(j.status==="queued"||j.status==="processing"?"Processing in background...":"Import complete");
    if(j.status==="queued"||j.status==="processing") void poll(j.id);
  }

  const completed=(progress?.imported_count||0)+(progress?.duplicate_count||0)+(progress?.failed_count||0);
  const total=progress?.discovered_count||0;
  const pct=total?Math.min(100,Math.round(completed/total*100)):0;

  return <form className="card section" onSubmit={submit}>
    <h2>Import activity history</h2>
    <p className="muted">Upload FIT, GPX, TCX, or a ZIP archive. Large imports run in background workers and can be checked after leaving this page.</p>
    <div className="row"><input className="input" type="file" name="files" multiple accept=".fit,.gpx,.tcx,.zip"/><button className="button">Upload</button></div>
    {status&&<p>{status}</p>}
    {progress&&<div className="importProgress">
      <div className="progressTrack"><div className="progressFill" style={{width:`${pct}%`}}/></div>
      <div className="row muted"><span>{progress.imported_count} imported</span><span>{progress.duplicate_count} duplicates</span><span>{progress.failed_count} failed</span><span>{progress.status}</span></div>
    </div>}
  </form>;
}
