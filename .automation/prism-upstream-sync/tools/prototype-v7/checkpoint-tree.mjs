// Offline verification only. This does not update a ref or validate audit claims.
import {execFileSync} from 'node:child_process';
const sha=x=>typeof x==='string' && /^[a-f0-9]{40}$/.test(x);
const prefix='.automation/prism-upstream-sync/';
function tree(repo, commit) {
  // Lossy decoding can collapse distinct non-UTF-8 Git paths into one map key.
  // Refuse such repositories rather than silently omitting a changed entry.
  const raw=new TextDecoder('utf-8',{fatal:true}).decode(execFileSync('git',['-C',repo,'ls-tree','-rz','--full-tree',commit],{maxBuffer:32*1024*1024}));
  return new Map(raw.split('\0').filter(Boolean).map(line=>{
    const m=/^(\d+) (blob|commit) ([a-f0-9]{40})\t([\s\S]+)$/.exec(line);
    if(!m) throw Error('malformed tree');
    return [m[4],{mode:m[1],type:m[2],sha:m[3]}];
  }));
}
export function verifyCheckpointTree(repo, original, candidate, changes) {
  if(typeof repo!=='string'||!repo||!sha(original)||!sha(candidate)||original===candidate||!Array.isArray(changes)||!changes.length) throw Error('invalid candidate request');
  const git=(...args)=>execFileSync('git',['-C',repo,...args],{encoding:'utf8'}).trim();
  const parents=git('rev-list','--parents','-n','1',candidate).split(' ');
  if(parents.length!==2||parents[0]!==candidate||parents[1]!==original) throw Error('candidate must be a direct single-parent child');
  const old=tree(repo,original), next=tree(repo,candidate), expected=new Map();
  for(const change of changes){
    const p=change?.path;
    if(typeof p!=='string'||!p.startsWith(prefix)||p===prefix+'locks/current.json'||p.includes('\0')||p.split('/').some(part=>!part||part==='.'||part==='..')||!sha(change.blob_sha)||expected.has(p)) throw Error('invalid or duplicate expected path');
    if(old.has(p)&&(p.startsWith(prefix+'runs/')||p.startsWith(prefix+'manifests/'))) throw Error('historical run and manifest artifacts are immutable');
    expected.set(p,change.blob_sha);
  }
  for(const [path,entry] of old){
    const actual=next.get(path);
    if(!actual) throw Error('checkpoint deletes existing content');
    if(!expected.has(path)&&(actual.sha!==entry.sha||actual.mode!==entry.mode||actual.type!==entry.type)) throw Error('checkpoint changes undeclared content');
  }
  for(const [path,entry] of next){
    if(!old.has(path)&&!expected.has(path)) throw Error('checkpoint adds undeclared content');
    if(expected.has(path)&&(entry.type!=='blob'||entry.mode!=='100644'||entry.sha!==expected.get(path))) throw Error('checkpoint content differs from expected blob');
  }
  for(const [path,blob] of expected){
    if(!next.has(path)) throw Error('expected checkpoint path absent');
    if(old.get(path)?.sha===blob&&old.get(path)?.type==='blob'&&old.get(path)?.mode==='100644') throw Error('declared checkpoint change is a no-op');
  }
  return {status:'TREE_VERIFIED_ONLY',original_sha:original,candidate_sha:candidate,tree_sha:git('rev-parse',`${candidate}^{tree}`),changed_paths:expected.size};
}
