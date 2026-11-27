import {test} from 'node:test';
import assert from 'node:assert/strict';
import {selectTask} from '../maintenance-engine.mjs';
import {recordId} from '../audit-record-validator.mjs';
import {mkdtempSync, writeFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {execFileSync} from 'node:child_process';
const h='a'.repeat(40),base='b'.repeat(40),blob='c'.repeat(40),old='d'.repeat(40);
const makeManifest=()=>({head_sha:h,base_sha:base,baseline_inventory_head_sha:h,baseline_tracked_inventory:[{path:'a',type:'blob',mode:'100644',blob_sha:blob}],related_inventory_paths:['a'],scheduled_audit_records:[]});
const state=()=>({schema_version:4,head_sha:h,base_sha:base,worker_v5:{phase:'AUDIT',work_id:'w',pr:43,head_sha:h,base_sha:base,expected_ci:{},audit_round:'R1',prior_stalled_count:0,blocker:null,next_exact_action:'review',queue:[]}});
const observed=()=>({head_sha:h,base_sha:base,state_sha:blob,pr_head_sha:h,pr_base_sha:base,lock:{status:'RELEASED'}});
const rec=(verdict='HELD')=>({path:'a',blob_sha:blob,head_sha:h,base_sha:base,round:'R1',owner:'scheduled',verdict,run_id:'run1',contract:'contract',negative_paths:['negative'],test_ids:['id'],review_evidence:'review',ci_evidence:{id:'fixture'}});
test('cursor reports distinct current counts and never promotes old or self-asserted PASS',()=>{
 const m=makeManifest();m.scheduled_audit_records=[{...rec('PASS'),head_sha:old}];let result=selectTask(state(),m,observed());assert.equal(result.status,'REVIEW');assert.deepEqual(result.counts,{reviewed:0,passed:0,held:0,defective:0,unverified_pass:0});
 m.scheduled_audit_records=[rec('PASS')];result=selectTask(state(),m,observed());assert.equal(result.status,'VERIFY_PASS');assert.equal(result.counts.passed,0);assert.equal(result.counts.unverified_pass,1);
 m.scheduled_audit_records=[rec('DEFECT')];result=selectTask(state(),m,observed());assert.equal(result.counts.defective,1);
});
test('an unverifiable PASS cannot starve a HELD or DEFECT revisit',()=>{
 const m=makeManifest();m.baseline_tracked_inventory.push({path:'b',type:'blob',mode:'100644',blob_sha:blob});m.related_inventory_paths.push('b');
 const pass=rec('PASS'),held={...rec('HELD'),path:'b',run_id:'run2'};m.scheduled_audit_records=[pass,held];
 let result=selectTask(state(),m,observed());assert.equal(result.status,'REVISIT');assert.equal(result.path,'b');assert.equal(result.counts.unverified_pass,1);assert.equal(result.counts.held,1);
 const fixed={...held,verdict:'DEFECT',run_id:'run3',review_evidence:'new evidence',supersedes:recordId(held)};m.scheduled_audit_records.push(fixed);
 result=selectTask(state(),m,observed());assert.equal(result.status,'REVISIT');assert.equal(result.path,'b');assert.equal(result.counts.defective,1);
});
test('cursor rejects duplicate, malformed and wrong-blob records but accepts explicit supersession',()=>{
 const m=makeManifest(),first=rec();m.scheduled_audit_records=[first,first];assert.throws(()=>selectTask(state(),m,observed()),/duplicate/);
 m.scheduled_audit_records=[{...first,blob_sha:old}];assert.throws(()=>selectTask(state(),m,observed()),/SHA mismatch/);
 const second={...rec('DEFECT'),run_id:'run2',review_evidence:'new',supersedes:recordId(first)};m.scheduled_audit_records=[first,second];const result=selectTask(state(),m,observed());assert.equal(result.counts.reviewed,1);assert.equal(result.counts.held,0);assert.equal(result.counts.defective,1);
});
test('stale HEAD and base suspend cursor before old evidence can count',()=>{
 const m=makeManifest();m.head_sha=old;assert.equal(selectTask(state(),m,observed()).status,'HEAD_INVALIDATED');m.head_sha=h;m.base_sha=old;assert.equal(selectTask(state(),m,observed()).status,'BASE_SUSPENDED');
});
test('CLI verifies old tree before reporting invalidated HEAD and rejects a tampered inventory',()=>{
 const dir=mkdtempSync(join(tmpdir(),'prism-stale-cursor-'));
 try {
  const git=(...args)=>execFileSync('git',['-C',dir,...args],{encoding:'utf8'}).trim();
  git('init','-q');git('config','user.name','Fixture');git('config','user.email','test@example.invalid');
  writeFileSync(join(dir,'a'),'old\n');git('add','a');git('commit','-qm','old');
  const original=git('rev-parse','HEAD'),tree=git('rev-parse','HEAD^{tree}'),sha=git('rev-parse','HEAD:a');
  writeFileSync(join(dir,'a'),'new\n');git('commit','-qam','new');const head=git('rev-parse','HEAD');
  const m={head_sha:original,base_sha:original,baseline_inventory_head_sha:original,freeze_tree_sha:tree,baseline_tracked_inventory:[{path:'a',type:'blob',mode:'100644',blob_sha:sha}],related_inventory_paths:['a'],scheduled_audit_records:[]};
  const s=state();s.head_sha=head;s.base_sha=original;s.worker_v5.head_sha=head;s.worker_v5.base_sha=original;
  const obs={...observed(),head_sha:head,base_sha:original,pr_head_sha:head,pr_base_sha:original};
  const input=join(dir,'input.json'), cli=new URL('../maintenance-engine.mjs',import.meta.url).pathname;
  writeFileSync(input,JSON.stringify({repo:dir,state:s,manifest:m,observed:obs}));
  const run=()=>JSON.parse(execFileSync(process.execPath,[cli,'select',input],{encoding:'utf8'}));
  assert.equal(run().status,'HEAD_INVALIDATED');
  m.baseline_tracked_inventory[0].blob_sha=blob;writeFileSync(input,JSON.stringify({repo:dir,state:s,manifest:m,observed:obs}));
  assert.throws(run,/Command failed/);
 } finally {rmSync(dir,{recursive:true,force:true});}
});
