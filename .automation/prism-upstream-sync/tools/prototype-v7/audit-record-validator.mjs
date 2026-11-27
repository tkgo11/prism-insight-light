// Offline evidence validation. This module never certifies an audit or authorizes a merge.
import { createHash } from 'node:crypto';
const sha = x => typeof x === 'string' && /^[0-9a-f]{40}$/.test(x);
const text = x => typeof x === 'string' && x.trim().length > 0;
const scope = x => [x.head_sha,x.base_sha,x.path,x.round].join('\0');
const digest = x => createHash('sha256').update(JSON.stringify(x)).digest('hex');
export const recordId = digest;

export function inventory(manifest, head, base, round) {
  if (!sha(head) || !sha(base) || manifest?.head_sha !== head || manifest.base_sha !== base || manifest.baseline_inventory_head_sha !== head) throw Error('frozen head/base mismatch');
  if (!['R1','R2','F1','F2'].includes(round)) throw Error('invalid round');
  const all = manifest.baseline_tracked_inventory, related = manifest.related_inventory_paths;
  if (!Array.isArray(all) || !all.length || !Array.isArray(related)) throw Error('missing inventory');
  const byPath = new Map();
  for (const item of all) {
    if (!text(item.path) || item.path.startsWith('/') || item.path.split('/').some(x => !x || x === '.' || x === '..') || byPath.has(item.path)) throw Error('invalid inventory path');
    if (item.type === 'blob') {
      if (!sha(item.blob_sha) || (item.object_sha != null && item.object_sha !== item.blob_sha) || !['100644','100755','120000'].includes(item.mode)) throw Error('invalid blob');
    } else if (item.type === 'commit') {
      if (!sha(item.object_sha) || item.blob_sha != null || item.mode !== '160000') throw Error('invalid gitlink');
    } else throw Error('invalid object type');
    byPath.set(item.path,item);
  }
  if (new Set(related).size !== related.length || related.some(p => !byPath.has(p))) throw Error('invalid related inventory');
  return {byPath, paths: (round[0] === 'R' ? related : all.map(x=>x.path)).slice().sort()};
}

export function currentRecords(records, head, base, round) {
  const latest = new Map();
  for (const r of records) if (r.head_sha === head && r.base_sha === base && r.round === round) latest.set(r.path,r);
  return latest;
}

// Existing history is untrusted input too. Validate same-scope chains before
// selecting a cursor; historical PASS is visible but never a certified pass.
export function summarizeRound(manifest,head,base,round) {
  const {byPath,paths}=inventory(manifest,head,base,round), records=manifest.scheduled_audit_records??[];
  if(!Array.isArray(records)) throw Error('malformed evidence history');
  const latest=new Map();
  for(const r of records) {
    if(!r||typeof r!=='object') throw Error('malformed evidence record');
    if(r.head_sha!==head||r.base_sha!==base||r.round!==round) continue;
    const item=byPath.get(r.path);
    if(!item||!paths.includes(r.path)||r.blob_sha!==item.blob_sha||(item.type!=='blob'&&(r.object_sha!==item.object_sha||r.type!==item.type||r.mode!==item.mode))) throw Error('evidence outside inventory or SHA mismatch');
    if(!['HELD','DEFECT','PASS'].includes(r.verdict)||!['scheduled','manual-user-request'].includes(r.owner)||!text(r.run_id)||!text(r.contract)||!Array.isArray(r.negative_paths)||!r.negative_paths.length||r.negative_paths.some(x=>!text(x))||!Array.isArray(r.test_ids)||!r.review_evidence||!r.ci_evidence) throw Error('incomplete audit record');
    const prior=latest.get(r.path);
    if(prior) {
      if(!['HELD','DEFECT'].includes(prior.verdict)||r.supersedes!==recordId(prior)||r.run_id===prior.run_id||(JSON.stringify(r.review_evidence)===JSON.stringify(prior.review_evidence)&&JSON.stringify(r.ci_evidence)===JSON.stringify(prior.ci_evidence))) throw Error('duplicate record or invalid supersession');
    } else if(r.supersedes) throw Error('unknown supersession');
    latest.set(r.path,r);
  }
  const values=[...latest.values()];
  return {paths,latest,reviewed:latest.size,passed:0,held:values.filter(r=>r.verdict==='HELD').length,defective:values.filter(r=>r.verdict==='DEFECT').length,unverified_pass:values.filter(r=>r.verdict==='PASS').length};
}

export function applyAuditRecord(manifest, record, head, base, round) {
  const {byPath, paths} = inventory(manifest,head,base,round);
  if (record.head_sha !== head || record.base_sha !== base || record.round !== round) throw Error('record scope mismatch');
  const item = byPath.get(record.path);
  if (!item || !paths.includes(record.path) || record.blob_sha !== item.blob_sha || (item.type !== 'blob' && (record.object_sha !== item.object_sha || record.type !== item.type || record.mode !== item.mode))) throw Error('record outside inventory or SHA mismatch');
  if (!['HELD','DEFECT','PASS'].includes(record.verdict) || !['scheduled','manual-user-request'].includes(record.owner) || !text(record.run_id) || !text(record.contract) || !Array.isArray(record.negative_paths) || !record.negative_paths.length || record.negative_paths.some(x=>!text(x)) || !Array.isArray(record.test_ids) || !record.review_evidence || !record.ci_evidence) throw Error('incomplete audit record');
  const records = manifest.scheduled_audit_records ?? [];
  summarizeRound(manifest,head,base,round);
  const prior = records.filter(x=>scope(x)===scope(record));
  if (prior.length) {
    const last = prior.at(-1);
    if (!['HELD','DEFECT'].includes(last.verdict) || record.supersedes !== recordId(last) || record.run_id === last.run_id || (JSON.stringify(record.review_evidence) === JSON.stringify(last.review_evidence) && JSON.stringify(record.ci_evidence) === JSON.stringify(last.ci_evidence))) throw Error('duplicate record or invalid supersession');
  } else if (record.supersedes) throw Error('unknown supersession');
  // No local boolean or self-asserted transcript can establish independent certification.
  // Until a trusted independent verifier is integrated, PASS must fail closed.
  if (record.verdict === 'PASS') throw Error('PASS requires trusted independent evidence verifier; unsupported');
  const result = [...records,record], current = currentRecords(result,head,base,round);
  return {records:result, reviewed:current.size, pass_count:0, held:[...current.values()].filter(x=>x.verdict==='HELD').length, defect:[...current.values()].filter(x=>x.verdict==='DEFECT').length, next:paths.find(x=>!current.has(x))??null};
}
