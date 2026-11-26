// Pure offline validator for PRISM evidence. Does not call GitHub, certify rounds, or authorize merges.
export function applyAuditRecord(manifest,record,head,base,round){
  if(manifest.head_sha!==head||manifest.base_sha!==base||manifest.baseline_inventory_head_sha!==head)throw Error("frozen head/base mismatch");
  if(!["R1","R2","F1","F2"].includes(round)||record.head_sha!==head||record.base_sha!==base||record.round!==round)throw Error("record scope mismatch");
  const all=manifest.baseline_tracked_inventory;
  if(!Array.isArray(all)||!Array.isArray(manifest.related_inventory_paths))throw Error("missing inventory");
  const byPath=new Map(all.map(x=>[x.path,x]));
  const paths=round.startsWith("R")?manifest.related_inventory_paths:all.map(x=>x.path);
  if(byPath.size!==all.length||new Set(paths).size!==paths.length||paths.some(x=>!byPath.has(x)))throw Error("invalid inventory");
  const item=byPath.get(record.path);
  if(!item||!paths.includes(record.path)||record.blob_sha!==item.blob_sha)throw Error("record outside inventory or SHA mismatch");
  if(item.type!=="blob"&&(record.object_sha!==item.object_sha||record.type!==item.type||record.mode!==item.mode))throw Error("non-blob object mismatch");
  if(!["HELD","DEFECT","PASS"].includes(record.verdict)||record.owner!=="scheduled"&&record.owner!=="manual-user-request"||!record.run_id||!record.contract||!Array.isArray(record.negative_paths)||!record.negative_paths.length||!Array.isArray(record.test_ids)||!record.review_evidence||!record.ci_evidence)throw Error("incomplete audit record");
  const records=manifest.scheduled_audit_records??[];
  const scoped=records.filter(x=>x.head_sha===head&&x.base_sha===base&&x.round===round);
  if(scoped.some(x=>x.path===record.path))throw Error("duplicate record; use separate supersession review");
  if(record.verdict==="PASS"&&(record.owner!=="scheduled"||record.ci_evidence.head_sha!==head||record.ci_evidence.checkout_verified!==true||record.ci_evidence.negative_execution_verified!==true||!record.ci_evidence.independent_execution_id||!record.test_ids.length||records.some(x=>x.ci_evidence?.independent_execution_id===record.ci_evidence.independent_execution_id&&x.round!==round)))throw Error("PASS evidence insufficient or reused across rounds");
  const result=[...records,record];
  const current=[...scoped,record],reviewed=new Set(current.map(x=>x.path)),passed=new Set(current.filter(x=>x.verdict==="PASS"&&x.owner==="scheduled").map(x=>x.path));
  return {records:result,reviewed:reviewed.size,pass_count:passed.size,held:current.filter(x=>x.verdict==="HELD").length,defect:current.filter(x=>x.verdict==="DEFECT").length,next:paths.find(x=>!reviewed.has(x))??null};
}
