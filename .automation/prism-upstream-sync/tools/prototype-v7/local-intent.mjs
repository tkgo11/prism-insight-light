// Local crash-resistant intent journal and synthetic CAS adapter. Never contacts GitHub.
import {constants,openSync,readFileSync,closeSync,fsyncSync,linkSync,unlinkSync,lstatSync} from 'node:fs';
import {dirname,resolve} from 'node:path';
import {createHash,randomUUID} from 'node:crypto';
import {reconcile} from './maintenance-engine.mjs';
const sha = x=>typeof x==='string'&&/^[0-9a-f]{40}$/.test(x);
const hash = x=>createHash('sha256').update(x,'utf8').digest('hex');
function canonical(x) {
  if(x===null || typeof x==='boolean' || typeof x==='string') return JSON.stringify(x);
  if(typeof x==='number' && Number.isSafeInteger(x)) return JSON.stringify(x);
  if(Array.isArray(x)) return '['+x.map(canonical).join(',')+']';
  if(x && Object.getPrototypeOf(x)===Object.prototype) return '{'+Object.keys(x).sort().map(k=>JSON.stringify(k)+':'+canonical(x[k])).join(',')+'}';
  throw Error('unsupported intent value');
}
function validateIntent(intent) {
  // The shared reconciliation schema rejects invalid/missing fields and invalid ref transitions.
  const observed={state_sha:intent?.original_state_sha,branch_head:intent?.original_head,pr_head:intent?.original_head,pr_base:intent?.base_sha,lock:{status:'RELEASED'}};
  reconcile(intent,observed);
}
function syncDirectory(path) {const fd=openSync(path,constants.O_RDONLY);try{fsyncSync(fd);}finally{closeSync(fd);}}
function readEnvelope(path) {
  const st=lstatSync(path); if(!st.isFile() || st.isSymbolicLink()) throw Error('intent is not a regular file');
  if(st.size>1048576) throw Error('intent too large');
  const fd=openSync(path,constants.O_RDONLY|constants.O_NOFOLLOW); let data;
  try {data=readFileSync(fd,'utf8');} finally {closeSync(fd);}
  if(Buffer.byteLength(data,'utf8')>1048576) throw Error('intent too large');
  let envelope;try{envelope=JSON.parse(data);}catch{throw Error('malformed intent file');}
  if(!envelope || envelope.schema_version!==1 || typeof envelope.intent_sha256!=='string' || !/^[0-9a-f]{64}$/.test(envelope.intent_sha256) || !envelope.intent || hash(canonical(envelope.intent))!==envelope.intent_sha256) throw Error('intent integrity mismatch');
  validateIntent(envelope.intent);return envelope;
}
export function loadIntent(path) {return readEnvelope(resolve(path));}
export function persistIntent(path,intent) {
  validateIntent(intent);
  const target=resolve(path),parent=dirname(target),canonicalIntent=canonical(intent);
  const envelope={schema_version:1,intent_sha256:hash(canonicalIntent),intent};
  const serialized=canonical(envelope)+'\n';
  const temp=target+'.tmp-'+randomUUID();
  let fd;
  try {
    fd=openSync(temp,constants.O_WRONLY|constants.O_CREAT|constants.O_EXCL|constants.O_NOFOLLOW,0o600);
    const bytes=Buffer.from(serialized,'utf8');let offset=0;
    while(offset<bytes.length) {const n=awaitlessWrite(fd,bytes,offset); if(n<=0) throw Error('short intent write'); offset+=n;}
    fsyncSync(fd);closeSync(fd);fd=undefined;
    try {linkSync(temp,target);} catch(e) {
      if(e.code!=='EEXIST') throw e;
      const existing=readEnvelope(target);
      if(existing.intent_sha256!==envelope.intent_sha256 || canonical(existing.intent)!==canonicalIntent) throw Error('intent already exists with different contents');
      syncDirectory(parent);
      return {status:'ALREADY_PERSISTED',intent_sha256:existing.intent_sha256};
    }
    syncDirectory(parent);
    const saved=readEnvelope(target);
    if(saved.intent_sha256!==envelope.intent_sha256) throw Error('persisted intent verification failed');
    return {status:'PERSISTED',intent_sha256:saved.intent_sha256};
  } finally {if(fd!==undefined)closeSync(fd);try{unlinkSync(temp);}catch(e){if(e.code!=='ENOENT')throw e;}}
}
// Explicit write wrapper keeps partial-write handling testable through OS behavior.
import {writeSync} from 'node:fs';
function awaitlessWrite(fd,bytes,offset){return writeSync(fd,bytes,offset,bytes.length-offset);}
export function createMockCas(initialSha) {
  if(!sha(initialSha)) throw Error('invalid mock ref');
  let current=initialSha, writes=0;
  return Object.freeze({read:()=>current, writes:()=>writes, update:({expected_sha,sha:candidate,force})=>{
    if(!sha(expected_sha)||!sha(candidate)||force!==false) throw Error('invalid mock CAS request');
    if(current===candidate) return {status:'ALREADY_AT_CANDIDATE',sha:current};
    if(current!==expected_sha) return {status:'CONFLICT',sha:current};
    current=candidate;writes++;return {status:'UPDATED',sha:current};
  }});
}
