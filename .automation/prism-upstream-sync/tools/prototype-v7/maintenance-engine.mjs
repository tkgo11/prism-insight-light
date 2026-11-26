#!/usr/bin/env node
// Deterministic, offline checkpoint preparation. No external writes or merge operation.
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { inventory, currentRecords, applyAuditRecord } from './audit-record-validator.mjs';
import { validateArtifact } from './evidence-artifact.mjs';
import { persistIntent, loadIntent } from './local-intent.mjs';
const sha = x => typeof x === 'string' && /^[0-9a-f]{40}$/.test(x);
const phases = new Set(['DISCOVER','WAIT_RED','FIX','WAIT_GREEN','AUDIT','MERGE_READY','SYNC']);
const required = ['phase','work_id','pr','head_sha','base_sha','expected_ci','audit_round','prior_stalled_count','blocker','next_exact_action','queue'];
function fail(s) { throw Error(s); }
export function validateState(state) {
  if (!state || state.schema_version !== 4 || !state.worker_v5 || required.some(k=>!(k in state.worker_v5))) fail('missing or unsupported v4/worker_v5 state');
  const w=state.worker_v5;
  if (!phases.has(w.phase) || !Number.isInteger(w.pr) || w.pr < 1 || !sha(w.head_sha) || !sha(w.base_sha) || state.head_sha !== w.head_sha || state.base_sha !== w.base_sha || !Array.isArray(w.queue) || !Number.isInteger(w.prior_stalled_count) || w.prior_stalled_count<0 || typeof w.next_exact_action !== 'string') fail('malformed worker_v5');
  return w;
}
export function verifyInventory(repo, manifest, head, base, round) {
  const {byPath,paths}=inventory(manifest,head,base,round);
  const git=(...args)=>execFileSync('git',['-C',repo,...args],{encoding:'utf8',maxBuffer:32*1024*1024}).trim();
  if (git('rev-parse',`${head}^{commit}`)!==head || git('rev-parse',`${head}^{tree}`)!==manifest.freeze_tree_sha || git('rev-parse',`${base}^{commit}`)!==base) fail('git head/base/tree mismatch');
  const raw=execFileSync('git',['-C',repo,'ls-tree','-rz','--full-tree',head],{maxBuffer:32*1024*1024}).toString();
  const entries=raw.split('\0').filter(Boolean).map(row=>{const m=/^(\d+) (blob|commit) ([0-9a-f]{40})\t([\s\S]+)$/.exec(row); if(!m) fail('malformed git tree'); return {mode:m[1],type:m[2],object:m[3],path:m[4]};});
  if(entries.length!==byPath.size) fail('tree inventory size mismatch');
  for(const e of entries){const item=byPath.get(e.path); if(!item || item.type!==e.type || item.mode!==e.mode || (item.type==='blob'?item.blob_sha:item.object_sha)!==e.object) fail('tree inventory mismatch');}
  return paths;
}
export function selectTask(state, manifest, observed) {
  const w=validateState(state);
  if (!observed || !sha(observed.head_sha) || !sha(observed.base_sha) || !sha(observed.state_sha)) fail('missing observed refs');
  if(observed.head_sha!==w.head_sha) return {status:'INVALIDATED',next_exact_action:'Re-freeze inventory and reset current certifications after exact-HEAD GREEN.'};
  if(observed.base_sha!==w.base_sha) return {status:'SUSPENDED',next_exact_action:'Verify base impact before resuming certifications.'};
  if(observed.pr_head_sha!==observed.head_sha || observed.pr_base_sha!==observed.base_sha) return {status:'METADATA_LAG',next_exact_action:'Re-read branch refs and PR metadata before mutation.'};
  if(!observed.lock || !['ACTIVE','RELEASED'].includes(observed.lock.status)) fail('missing lock');
  if(observed.lock.status==='ACTIVE') return {status:'LOCKED',next_exact_action:'Do not mutate; verify lock ownership or obtain explicit recovery authorization.'};
  if(w.prior_stalled_count>=2){
    // Independence must be explicit; queue order does not determine priority.
    const candidates=w.queue.filter(x=>x && typeof x.work_id==='string' && x.work_id!==w.work_id && ['RED_REPRODUCED','EVIDENCE_REQUIRED','DESIGN_READY'].includes(x.status) && Array.isArray(x.independent_of) && x.independent_of.includes(w.work_id) && typeof x.next==='string' && x.next.trim());
    if(new Set(candidates.map(x=>x.work_id)).size!==candidates.length) fail('ambiguous duplicate independent tasks');
    candidates.sort((a,b)=>({P0:0,P1:1,P2:2}[a.priority]??3)-({P0:0,P1:1,P2:2}[b.priority]??3) || (a.work_id<b.work_id?-1:a.work_id>b.work_id?1:0));
    const task=candidates[0];
    if(task) return {status:'ALTERNATIVE',work_id:task.work_id,blocked_work_id:w.work_id,next_exact_action:task.next};
    return {status:'STALLED',blocked_work_id:w.work_id,next_exact_action:'Investigate the repeated blocker or identify and explicitly mark an independent safe task.'};
  }
  if(w.phase==='AUDIT') {
    const {paths}=inventory(manifest,w.head_sha,w.base_sha,w.audit_round);
    const current=currentRecords(manifest.scheduled_audit_records??[],w.head_sha,w.base_sha,w.audit_round);
    const next=paths.find(p=>!current.has(p));
    const held=paths.find(p=>['HELD','DEFECT'].includes(current.get(p)?.verdict));
    return {status:next?'REVIEW':held?'REVISIT':'PENDING_CERTIFICATION',path:next??held??null,next_exact_action:next?`Review ${next} independently.`:held?`Revisit ${held} with new evidence and explicit supersession.`:'Verify all independent certifications and merge gates.'};
  }
  return {status:'RESUME',work_id:w.work_id,next_exact_action:w.next_exact_action};
}
export function prepareCheckpoint(state, nextState, observed, recheck, previousManifest, nextManifest) {
  validateState(state); validateState(nextState);
  const oldRecords=previousManifest?.scheduled_audit_records, newRecords=nextManifest?.scheduled_audit_records;
  if (!Array.isArray(oldRecords) || !Array.isArray(newRecords) || newRecords.length < oldRecords.length || oldRecords.some((r,i)=>JSON.stringify(r)!==JSON.stringify(newRecords[i]))) fail('evidence history is not append-only');
  if (!previousManifest || !nextManifest || previousManifest.head_sha!==nextManifest.head_sha || previousManifest.base_sha!==nextManifest.base_sha || previousManifest.freeze_tree_sha!==nextManifest.freeze_tree_sha || JSON.stringify(previousManifest.baseline_tracked_inventory)!==JSON.stringify(nextManifest.baseline_tracked_inventory) || JSON.stringify(previousManifest.related_inventory_paths)!==JSON.stringify(nextManifest.related_inventory_paths)) fail('checkpoint freeze changed');
  let history=oldRecords.slice();
  for (const record of newRecords.slice(oldRecords.length)) { const result=applyAuditRecord({...previousManifest,scheduled_audit_records:history},record,previousManifest.head_sha,previousManifest.base_sha,record.round); history=result.records; }
  if (!observed || !recheck || !sha(observed.state_sha) || observed.lock?.status!=='RELEASED' || recheck.lock?.status!=='RELEASED') fail('checkpoint requires released lock');
  for(const k of ['state_sha','head_sha','base_sha','pr_head_sha','pr_base_sha']) if(!sha(observed[k]) || observed[k]!==recheck[k]) fail('CAS precondition changed');
  if(observed.head_sha!==state.worker_v5.head_sha || observed.base_sha!==state.worker_v5.base_sha || observed.pr_head_sha!==observed.head_sha || observed.pr_base_sha!==observed.base_sha) fail('checkpoint stale or PR metadata lag');
  if(nextState.worker_v5.head_sha!==observed.head_sha || nextState.worker_v5.base_sha!==observed.base_sha) fail('checkpoint scope mismatch');
  return {status:'PREPARED_ONLY',expected_sha:observed.state_sha,force:false,next_exact_action:'Create complete candidate commit, recheck preconditions, then use authorized compare-and-swap adapter and verify persisted contents.'};
}
export function reconcile(intent, observed) {
  const validText=x=>typeof x==='string' && x.trim().length>0;
  if(!intent || intent.schema_version!==1 || !['STATE_CHECKPOINT','CODE_MUTATION'].includes(intent.kind) || !validText(intent.run_id) || !validText(intent.owner) || !validText(intent.purpose) || !sha(intent.original_state_sha) || !sha(intent.candidate_state_sha) || !sha(intent.original_head) || !sha(intent.candidate_head) || !sha(intent.base_sha) || !/^[0-9a-f]{64}$/.test(intent.candidate_content_sha256??'') || intent.original_state_sha===intent.candidate_state_sha || !observed || !sha(observed.state_sha) || !sha(observed.branch_head) || !sha(observed.pr_head) || !sha(observed.pr_base) || !observed.lock || !['ACTIVE','RELEASED'].includes(observed.lock.status)) fail('malformed intent or observations');
  if(intent.kind==='STATE_CHECKPOINT' && intent.original_head!==intent.candidate_head || intent.kind==='CODE_MUTATION' && intent.original_head===intent.candidate_head) fail('invalid intent ref transition');
  if(observed.lock.status==='ACTIVE' && (intent.kind==='STATE_CHECKPOINT' || observed.lock.run_id!==intent.run_id || observed.lock.owner!==intent.owner)) return {status:'FOREIGN_OR_UNEXPECTED_LOCK',next_exact_action:'Do not mutate or release; verify ownership or obtain explicit recovery authorization.'};
  if(observed.pr_base!==intent.base_sha) return {status:'BASE_CHANGED',next_exact_action:'Stop and revalidate base impact; preserve intent.'};
  if(![intent.original_head,intent.candidate_head].includes(observed.branch_head) || ![intent.original_state_sha,intent.candidate_state_sha].includes(observed.state_sha)) return {status:'CONFLICT',next_exact_action:'Stop and inspect unexpected ref; preserve intent.'};
  if(observed.pr_head!==observed.branch_head) return {status:'METADATA_LAG',next_exact_action:'Re-read branch ref and PR HEAD separately; do not repeat mutation.'};
  if(intent.kind==='CODE_MUTATION' && observed.branch_head===intent.original_head) {
    if(observed.state_sha!==intent.original_state_sha) return {status:'CONFLICT',next_exact_action:'Inspect checkpoint with unapplied code mutation; preserve intent.'};
    return {status:'NOT_APPLIED',next_exact_action:'Revalidate all preconditions before retrying; do not duplicate work.'};
  }
  if(intent.kind==='CODE_MUTATION' && observed.lock.status!=='ACTIVE' && observed.state_sha===intent.original_state_sha) return {status:'RECOVERY_REQUIRED',next_exact_action:'Code ref moved but no owned ACTIVE lock or completed checkpoint is visible; investigate without mutation.'};
  if(observed.state_sha===intent.original_state_sha) return {status:intent.kind==='CODE_MUTATION'?'CODE_APPLIED_CHECKPOINT_MISSING':'NOT_APPLIED',next_exact_action:intent.kind==='CODE_MUTATION'?'Verify owned lock and prepare checkpoint; do not repeat code mutation.':'Revalidate preconditions before checkpoint CAS.'};
  if(typeof observed.checkpoint_content!=='string') return {status:'PERSISTED_CONTENT_UNVERIFIED',next_exact_action:'Read actual persisted checkpoint content and verify its digest.'};
  const actual=createHash('sha256').update(observed.checkpoint_content).digest('hex');
  if(actual!==intent.candidate_content_sha256) return {status:'PARTIAL_CHECKPOINT',next_exact_action:'Stop; persisted content differs from the prepared checkpoint. Preserve evidence and investigate.'};
  if(observed.lock.status==='ACTIVE') return {status:'CHECKPOINT_VERIFIED_LOCK_HELD',next_exact_action:'Verify ownership and release the lock; do not repeat the mutation.'};
  return {status:'COMPLETE',next_exact_action:'No repeat mutation; resume the next persisted action.'};
}

function main(){
  try { const [command,file]=process.argv.slice(2); if(!file) fail('usage: node maintenance-engine.mjs select|prepare|reconcile|evidence|persist-intent|load-intent input.json'); const x=JSON.parse(readFileSync(file,'utf8')); let result;
    if(command==='select') { if(!x.repo) fail('repository required for tree verification'); const w=validateState(x.state); verifyInventory(x.repo,x.manifest,w.head_sha,w.base_sha,w.audit_round); result=selectTask(x.state,x.manifest,x.observed); }
    else if(command==='prepare') result=prepareCheckpoint(x.state,x.next_state,x.observed,x.recheck,x.previous_manifest,x.next_manifest);
    else if(command==='reconcile') result=reconcile(x.intent,x.observed);
    else if(command==='evidence') result=validateArtifact(x.artifact,x.expected,x.history);
    else if(command==='persist-intent') result=persistIntent(x.path,x.intent);
    else if(command==='load-intent') result=loadIntent(x.path);
    else fail('unknown command; merge is never supported');
    process.stdout.write(JSON.stringify(result)+'\n');
  } catch(e) { process.stdout.write(JSON.stringify({status:'ERROR',error:e.message})+'\n'); process.exitCode=1; }
}
if(process.argv[1] && import.meta.url===new URL(`file://${process.argv[1]}`).href) main();
