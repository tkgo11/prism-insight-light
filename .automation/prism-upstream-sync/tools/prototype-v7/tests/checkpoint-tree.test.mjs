import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,mkdirSync,writeFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {execFileSync} from 'node:child_process';
import {verifyCheckpointTree} from '../checkpoint-tree.mjs';

test('candidate direct child must preserve all undeclared files and exactly match declared blob changes',()=>{
 const dir=mkdtempSync(join(tmpdir(),'checkpoint-tree-'));
 try {
  const git=(...args)=>execFileSync('git',['-C',dir,...args],{encoding:'utf8'}).trim();
  git('init','-q');git('config','user.name','Test');git('config','user.email','test@example.invalid');
  const p='.automation/prism-upstream-sync/';mkdirSync(join(dir,p,'locks'),{recursive:true});
  writeFileSync(join(dir,p,'latest-run.json'),'old');writeFileSync(join(dir,p,'locks/current.json'),'released');writeFileSync(join(dir,'untouched'),'keep');
  git('add','.');git('commit','-qm','initial');const old=git('rev-parse','HEAD');
  writeFileSync(join(dir,p,'latest-run.json'),'new');writeFileSync(join(dir,p,'evidence.json'),'evidence');
  git('add','.');git('commit','-qm','candidate');const next=git('rev-parse','HEAD');
  const changes=['latest-run.json','evidence.json'].map(n=>({path:p+n,blob_sha:git('rev-parse',`${next}:${p}${n}`)}));
  assert.equal(verifyCheckpointTree(dir,old,next,changes).status,'TREE_VERIFIED_ONLY');
  assert.throws(()=>verifyCheckpointTree(dir,old,next,changes.slice(0,1)),/undeclared/);
  assert.throws(()=>verifyCheckpointTree(dir,old,next,[...changes,{...changes[0]}]),/duplicate/);
  assert.throws(()=>verifyCheckpointTree(dir,old,next,[{...changes[0],blob_sha:'a'.repeat(40)},changes[1]]),/differs/);
  assert.throws(()=>verifyCheckpointTree(dir,old,next,[...changes,{path:p+'locks/current.json',blob_sha:'a'.repeat(40)}]),/invalid/);
  writeFileSync(join(dir,'untouched'),'tampered');git('add','.');git('commit','-qm','extra');const extra=git('rev-parse','HEAD');
  assert.throws(()=>verifyCheckpointTree(dir,old,extra,changes),/single-parent/);
  assert.throws(()=>verifyCheckpointTree(dir,next,extra,[{path:p+'../untouched',blob_sha:'a'.repeat(40)}]),/invalid/);
 } finally {rmSync(dir,{recursive:true,force:true});}
});

test('rejects deletion, executable replacement and rewriting prior evidence',()=>{
 const dir=mkdtempSync(join(tmpdir(),'checkpoint-damage-'));
 try {
  const git=(...args)=>execFileSync('git',['-C',dir,...args],{encoding:'utf8'}).trim();
  git('init','-q');git('config','user.name','Test');git('config','user.email','test@example.invalid');
  const p='.automation/prism-upstream-sync/';mkdirSync(join(dir,p,'runs'),{recursive:true});mkdirSync(join(dir,p,'manifests'),{recursive:true});
  writeFileSync(join(dir,p,'latest-run.json'),'one');writeFileSync(join(dir,p,'runs/old.json'),'evidence');writeFileSync(join(dir,p,'manifests/old.json'),'manifest');
  git('add','.');git('commit','-qm','initial');const old=git('rev-parse','HEAD');
  writeFileSync(join(dir,p,'latest-run.json'),'two');rmSync(join(dir,p,'runs/old.json'));git('add','-A');git('commit','-qm','deletion');
  let next=git('rev-parse','HEAD');let change={path:p+'latest-run.json',blob_sha:git('rev-parse',`${next}:${p}latest-run.json`)};
  assert.throws(()=>verifyCheckpointTree(dir,old,next,[change]),/deletes/);
  git('reset','--hard',old);writeFileSync(join(dir,p,'runs/old.json'),'changed');git('add','.');git('commit','-qm','changed evidence');
  next=git('rev-parse','HEAD');change={path:p+'runs/old.json',blob_sha:git('rev-parse',`${next}:${p}runs/old.json`)};
  assert.throws(()=>verifyCheckpointTree(dir,old,next,[change]),/immutable/);
  git('reset','--hard',old);writeFileSync(join(dir,p,'manifests/old.json'),'changed');git('add','.');git('commit','-qm','changed manifest');
  next=git('rev-parse','HEAD');change={path:p+'manifests/old.json',blob_sha:git('rev-parse',`${next}:${p}manifests/old.json`)};
  assert.throws(()=>verifyCheckpointTree(dir,old,next,[change]),/immutable/);
  git('reset','--hard',old);writeFileSync(join(dir,p,'latest-run.json'),'two');git('add','.');git('update-index','--chmod=+x',p+'latest-run.json');git('commit','-qm','executable');
  next=git('rev-parse','HEAD');change={path:p+'latest-run.json',blob_sha:git('rev-parse',`${next}:${p}latest-run.json`)};
  assert.throws(()=>verifyCheckpointTree(dir,old,next,[change]),/differs/);
 } finally {rmSync(dir,{recursive:true,force:true});}
});

test('non-UTF-8 Git paths fail closed instead of collapsing to replacement characters',()=>{
 const dir=mkdtempSync(join(tmpdir(),'checkpoint-nonutf8-'));
 try {
  const git=(...args)=>execFileSync('git',['-C',dir,...args],{encoding:'utf8'}).trim();
  git('init','-q');git('config','user.name','Test');git('config','user.email','test@example.invalid');
  const p='.automation/prism-upstream-sync/';mkdirSync(join(dir,p),{recursive:true});
  writeFileSync(join(dir,p,'latest-run.json'),'one');writeFileSync(Buffer.concat([Buffer.from(dir+'/'),Buffer.from([0xff])]),'opaque');
  git('add','.');git('commit','-qm','initial');const old=git('rev-parse','HEAD');
  writeFileSync(join(dir,p,'latest-run.json'),'two');git('add','.');git('commit','-qm','candidate');const next=git('rev-parse','HEAD');
  assert.throws(()=>verifyCheckpointTree(dir,old,next,[{path:p+'latest-run.json',blob_sha:git('rev-parse',`${next}:${p}latest-run.json`)}]),/encoded data|encoding/i);
 } finally {rmSync(dir,{recursive:true,force:true});}
});
