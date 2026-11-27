// Synthetic adapter and offline orchestration; this module cannot address a remote.
import {createHash} from 'node:crypto';
import {persistIntent,loadIntent} from './local-intent.mjs';
import {reconcile} from './maintenance-engine.mjs';
const known=new WeakSet();
const sha=x=>typeof x==='string' && /^[0-9a-f]{40}$/.test(x);
const digest=x=>createHash('sha256').update(x,'utf8').digest('hex');
const clone=x=>structuredClone(x);
export function createMockCheckpointAdapter(initial) {
  let data=clone(initial), writes=0;
  if(!data || !sha(data.state_sha) || !sha(data.branch_head) || !sha(data.pr_head) || !sha(data.pr_base) || !data.lock || typeof data.checkpoint_content!=='string') throw Error('invalid mock snapshot');
  const adapter=Object.freeze({
    snapshot:()=>clone(data),
    writes:()=>writes,
    compareAndSwap:({expected_sha,candidate_sha,expected_head,expected_base,content,force})=>{
      if(!sha(expected_sha)||!sha(candidate_sha)||!sha(expected_head)||!sha(expected_base)||typeof content!=='string'||force!==false) throw Error('invalid mock CAS request');
      if(data.state_sha!==expected_sha || data.branch_head!==expected_head || data.pr_head!==expected_head || data.pr_base!==expected_base || data.lock?.status!=='RELEASED') return {status:'CONFLICT',actual_sha:data.state_sha};
      data={...data,state_sha:candidate_sha,checkpoint_content:content};writes++;
      return {status:'UPDATED',sha:candidate_sha};
    },
    // Simulates external concurrent actors; available only on this in-memory fixture.
    change:(patch)=>{data={...data,...clone(patch)};},
  });
  known.add(adapter);return adapter;
}
export function executeMockCheckpoint({path,intent,candidateContent,adapter,beforeCas,afterCas}) {
  if(!known.has(adapter)) throw Error('only built-in mock adapter is accepted');
  if(intent?.kind!=='STATE_CHECKPOINT' || typeof candidateContent!=='string' || digest(candidateContent)!==intent.candidate_content_sha256) throw Error('invalid checkpoint candidate');
  // No ref change can precede durable intent persistence and readback.
  persistIntent(path,intent);
  const saved=loadIntent(path).intent;
  let observed=adapter.snapshot(), result=reconcile(saved,observed);
  if(result.status!=='NOT_APPLIED') return result;
  if(typeof beforeCas==='function') beforeCas(adapter); // Test-only simulated race.
  observed=adapter.snapshot(); result=reconcile(saved,observed);
  if(result.status!=='NOT_APPLIED') return result;
  const update=adapter.compareAndSwap({expected_sha:saved.original_state_sha,candidate_sha:saved.candidate_state_sha,expected_head:saved.original_head,expected_base:saved.base_sha,content:candidateContent,force:false});
  if(update.status!=='UPDATED') return {status:'CONFLICT',next_exact_action:'Discard candidate and re-read state before recomputing.'};
  if(typeof afterCas==='function') afterCas(adapter); // Test-only simulated interruption.
  return reconcile(saved,adapter.snapshot());
}
