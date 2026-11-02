/**
 * Browser contract aligned with the backend public DTOs. No credentials in DTOs.
 * @typedef {{status:string, model_configured:boolean, model_verified:boolean,
 * data_sources_ready:boolean, reference_date:string|null}} Health
 * @typedef {{session_id:string, reference_date:string|null, data_labels:string[]}} Session
 * @typedef {{citation_id:string,source_id:string,page:number|null,section:string|null,excerpt:string,title:string}} Citation
 * @typedef {{chart_id:string,title:string}} Chart
 * @typedef {'run_started'|'text_delta'|'tool_started'|'tool_completed'|'tool_error'|'retry'|'citation'|'chart'|'run_completed'|'run_error'} EventType
 * @typedef {{run_id:string,seq:number,type:EventType,time:string,payload:Record<string,unknown>}} RunEvent
 * @typedef {{signal:AbortSignal,onEvent:(event:RunEvent)=>void}} StreamOptions
 * @typedef {Object} ApiClient
 * @property {()=>Promise<Health>} health
 * @property {()=>Promise<Session>} createSession
 * @property {(sessionId:string)=>Promise<void>} deleteSession
 * @property {(sessionId:string,message:string,options:StreamOptions)=>Promise<void>} chat
 * @property {(sourceId:string,page:number|null)=>string} documentUrl
 * @property {(sessionId:string,chartId:string)=>string} chartUrl
 */
export {};