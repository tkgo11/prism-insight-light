import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,mkdirSync,writeFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {execFileSync} from 'node:child_process';
import {verifyCheckpointCandidate,evaluateRefreeze} from '../maintenance-engine.mjs';

test('actual candidate blobs must contain valid state and preserve existing audit evidence',()=>{
 const dir=mkdtempSync(join(tmpdir(),'checkpoint-candidate-'));
 try {
  const git=(...args)=>execFileSync('git',['-C',dir,...args],{encoding:'utf8'}).trim();
  git('init','-q');git('config','user.name','Test');git('config','user.email','test@example.invalid');
  writeFileSync(join(dir,'source'),'source');git('add','.');git('commit','-qm','code');
  const h=git('rev-parse','HEAD'),t=git('rev-parse','HEAD^{tree}'),blob=git('rev-parse','HEAD:source');
  const prefix='.automation/prism-upstream-sync/', oldManifest=prefix+'manifests/old.json', newManifest=prefix+'manifests/new.json';
  mkdirSync(join(dir,prefix,'manifests'),{recursive:true});
  const record={path:'source',head_sha:h,base_sha:h,round:'R1',verdict:'HELD'};
  const manifest={head_sha:h,base_sha:h,baseline_inventory_head_sha:h,freeze_tree_sha:t,baseline_tracked_inventory:[{path:'source',type:'blob',mode:'100644',blob_sha:blob}],related_inventory_paths:['source'],scheduled_audit_records:[record]};
  const state={schema_version:4,head_sha:h,base_sha:h,manifest_path:oldManifest,worker_v5:{phase:'AUDIT',work_id:'one',pr:43,head_sha:h,base_sha:h,expected_ci:{},audit_round:'R1',prior_stalled_count:0,blocker:null,next_exact_action:'review',queue:[]}};
  writeFileSync(join(dir,oldManifest),JSON.stringify(manifest));writeFileSync(join(dir,prefix,'latest-run.json'),JSON.stringify(state));
  git('add','.');git('commit','-qm','state');const original=git('rev-parse','HEAD');
  const observed={state_sha:original,head_sha:h,base_sha:h,pr_head_sha:h,pr_base_sha:h,lock:{status:'RELEASED'}};
  const commitCandidate=(s,m)=>{writeFileSync(join(dir,prefix,'latest-run.json'),s);writeFileSync(join(dir,newManifest),JSON.stringify(m));git('add','.');git('commit','-qm','candidate');const candidate=git('rev-parse','HEAD');return {repo:dir,original_sha:original,candidate_sha:candidate,changes:[prefix+'latest-run.json',newManifest].map(path=>({path,blob_sha:git('rev-parse',`${candidate}:${path}`)})),observed,recheck:observed};};
  const candidate=commitCandidate(JSON.stringify({...state,manifest_path:newManifest}),manifest);
  assert.equal(verifyCheckpointCandidate(candidate).status,'CANDIDATE_VERIFIED_ONLY');
  git('reset','--hard',original);
  assert.throws(()=>verifyCheckpointCandidate(commitCandidate('not json',manifest)));
  git('reset','--hard',original);
  assert.throws(()=>verifyCheckpointCandidate(commitCandidate(JSON.stringify({...state,manifest_path:newManifest}),{...manifest,scheduled_audit_records:[]})),/append-only/);
  assert.throws(()=>verifyCheckpointCandidate({...candidate,observed:{...observed,state_sha:h}}),/observed state ref/);
  git('reset','--hard',original);
  const falsified={...state,manifest_path:newManifest,related_passes:2,full_passes:2,worker_v5:{...state.worker_v5,phase:'MERGE_READY'}};
  assert.throws(()=>verifyCheckpointCandidate(commitCandidate(JSON.stringify(falsified),manifest)),/completion unsupported/);
  git('reset','--hard',original);
  const falsifiedCount={...state,manifest_path:newManifest,related_passes:2};
  assert.throws(()=>verifyCheckpointCandidate(commitCandidate(JSON.stringify(falsifiedCount),manifest)),/counts unsupported/);
 } finally {rmSync(dir,{recursive:true,force:true});}
});

test('pre-freeze HEAD and base changes preserve historical evidence without audit certification',()=>{
 const dir=mkdtempSync(join(tmpdir(),'checkpoint-stale-'));
 try {
  const git=(...args)=>execFileSync('git',['-C',dir,...args],{encoding:'utf8'}).trim();
  git('init','-q');git('config','user.name','Test');git('config','user.email','test@example.invalid');
  writeFileSync(join(dir,'source'),'old');git('add','.');git('commit','-qm','old code');const old=git('rev-parse','HEAD'),tree=git('rev-parse','HEAD^{tree}'),blob=git('rev-parse','HEAD:source');
  writeFileSync(join(dir,'source'),'new');git('add','.');git('commit','-qm','new code');const current=git('rev-parse','HEAD');
  const p='.automation/prism-upstream-sync/',manifestPath=p+'manifests/old.json';mkdirSync(join(dir,p,'manifests'),{recursive:true});
  const history=[{path:'source',head_sha:old,base_sha:old,round:'R1',verdict:'HELD'}];
  const manifest={head_sha:old,base_sha:old,baseline_inventory_head_sha:old,freeze_tree_sha:tree,baseline_tracked_inventory:[{path:'source',type:'blob',mode:'100644',blob_sha:blob}],related_inventory_paths:['source'],scheduled_audit_records:history};
  const state=(head,base)=>({schema_version:4,head_sha:head,base_sha:base,manifest_path:manifestPath,related_passes:0,full_passes:0,worker_v5:{phase:'FIX',work_id:'one',pr:43,head_sha:head,base_sha:base,expected_ci:{},audit_round:'R1',prior_stalled_count:0,blocker:null,next_exact_action:'repair',queue:[]}});
  writeFileSync(join(dir,manifestPath),JSON.stringify(manifest));writeFileSync(join(dir,p,'latest-run.json'),JSON.stringify(state(current,old)));git('add','.');git('commit','-qm','state');const original=git('rev-parse','HEAD');
  const next={...state(current,old),ci_status:'PENDING'};writeFileSync(join(dir,p,'latest-run.json'),JSON.stringify(next));git('add','.');git('commit','-qm','checkpoint');const candidate=git('rev-parse','HEAD');
  const observed={state_sha:original,head_sha:current,base_sha:old,pr_head_sha:current,pr_base_sha:old,lock:{status:'RELEASED'}};
  const input={repo:dir,original_sha:original,candidate_sha:candidate,changes:[{path:p+'latest-run.json',blob_sha:git('rev-parse',`${candidate}:${p}latest-run.json`)}],observed,recheck:observed};
  assert.equal(verifyCheckpointCandidate(input).freeze_status,'HEAD_INVALIDATED');
  git('reset','--hard',original);const falseCi={...next,ci_status:'SUCCESS',ci_head_sha:current,ci_run_id:100};writeFileSync(join(dir,p,'latest-run.json'),JSON.stringify(falseCi));git('add','.');git('commit','-qm','unverified ci');const falseCiCommit=git('rev-parse','HEAD');
  assert.throws(()=>verifyCheckpointCandidate({...input,candidate_sha:falseCiCommit,changes:[{path:p+'latest-run.json',blob_sha:git('rev-parse',`${falseCiCommit}:${p}latest-run.json`)}]}),/trusted independent verifier/);
  git('reset','--hard',original);const unsafe={...next,worker_v5:{...next.worker_v5,phase:'AUDIT'}};writeFileSync(join(dir,p,'latest-run.json'),JSON.stringify(unsafe));git('add','.');git('commit','-qm','unsafe audit');const bad=git('rev-parse','HEAD');
  assert.throws(()=>verifyCheckpointCandidate({...input,candidate_sha:bad,changes:[{path:p+'latest-run.json',blob_sha:git('rev-parse',`${bad}:${p}latest-run.json`)}]}),/historical freeze/);
  // A complete proposed new freeze is still refused without trusted exact-HEAD CI provenance.
  git('reset','--hard',original);const newPath=p+'manifests/current.json';
  const currentManifest={...manifest,head_sha:current,baseline_inventory_head_sha:current,freeze_tree_sha:git('rev-parse',`${current}^{tree}`),baseline_tracked_inventory:[{path:'source',type:'blob',mode:'100644',blob_sha:git('rev-parse',`${current}:source`)}]};
  writeFileSync(join(dir,newPath),JSON.stringify(currentManifest));writeFileSync(join(dir,p,'latest-run.json'),JSON.stringify({...next,manifest_path:newPath}));git('add','.');git('commit','-qm','proposed freeze');const proposed=git('rev-parse','HEAD');
  const proposal={...input,candidate_sha:proposed,changes:[p+'latest-run.json',newPath].map(path=>({path,blob_sha:git('rev-parse',`${proposed}:${path}`)}))};
  assert.equal(evaluateRefreeze(proposal).status,'BLOCKED_UNVERIFIED_CI');
  assert.throws(()=>evaluateRefreeze({...proposal,observed:{...observed,base_sha:current}}),/precondition/);
  git('reset','--hard',original);writeFileSync(join(dir,newPath),JSON.stringify({...currentManifest,scheduled_audit_records:[]}));writeFileSync(join(dir,p,'latest-run.json'),JSON.stringify({...next,manifest_path:newPath}));git('add','.');git('commit','-qm','damaged history');const damaged=git('rev-parse','HEAD');
  assert.throws(()=>evaluateRefreeze({...input,candidate_sha:damaged,changes:[p+'latest-run.json',newPath].map(path=>({path,blob_sha:git('rev-parse',`${damaged}:${path}`)}))}),/preserve historical evidence/);
  // Same frozen HEAD with a different current base suspends the freeze.
  git('reset','--hard',current);mkdirSync(join(dir,p,'manifests'),{recursive:true});writeFileSync(join(dir,manifestPath),JSON.stringify(manifest));writeFileSync(join(dir,p,'latest-run.json'),JSON.stringify(state(old,current)));git('add','.');git('commit','-qm','base changed');const state2=git('rev-parse','HEAD');
  writeFileSync(join(dir,p,'latest-run.json'),JSON.stringify({...state(old,current),ci_status:'PENDING'}));git('add','.');git('commit','-qm','checkpoint');const candidate2=git('rev-parse','HEAD');
  const observed2={...observed,state_sha:state2,head_sha:old,base_sha:current,pr_head_sha:old,pr_base_sha:current};
  assert.equal(verifyCheckpointCandidate({repo:dir,original_sha:state2,candidate_sha:candidate2,changes:[{path:p+'latest-run.json',blob_sha:git('rev-parse',`${candidate2}:${p}latest-run.json`)}],observed:observed2,recheck:observed2}).freeze_status,'BASE_SUSPENDED');
 } finally {rmSync(dir,{recursive:true,force:true});}
});
