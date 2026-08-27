import UploadForm from "./UploadForm";
import StravaConnection from "./StravaConnection";
export default function Page(){return <><h1>Imports & connections</h1><p className="muted">Bring your complete training history into the athlete model. Strava backfills are paginated and rate-limit aware; file imports run through the same canonical activity pipeline.</p><StravaConnection/><UploadForm/></>}
