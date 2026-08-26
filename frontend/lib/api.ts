const API=process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";
export async function getJSON<T>(path:string):Promise<T>{const r=await fetch(API+path,{cache:"no-store"});if(!r.ok) throw new Error(await r.text());return r.json()}
export {API};
