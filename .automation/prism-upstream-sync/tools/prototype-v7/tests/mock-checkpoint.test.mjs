import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,rmSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {createMockCheckpointAdapter,executeMockCheckpoint} from '../mock-checkpoint.mjs';
const a='a'.repeat(40),b='b'.repeat(40),c='c'.repeat(40),d='d'.repeat(40),e='e'.repeat(40),content='prepared state';
const intent=()=>({schema_version:1,kind:'STATE_CHECKPOINT',run_id:'run',owner:'coordinator',purpose:'fixture',original_state_sha:c,candidate_state_sha:d,original_head:a,candidate_head:a,base_sha:b,candidate_content_sha256:createHash('sha256').update(content).digest('hex')});
const initial=()=>({state_sha:c,branch_head:a,pr_head:a,pr_base:b,lock:{status:'RELEASED'},checkpoint_content:'old state'});
function fixture(fn){const dir=mkdtempSync(join(tmpdir(),'prism-cas-'));try{fn(join(dir,'intent.json'));}finally{rmSync(dir,{recursive:true,force:true});}}
const run=(path,adapter,extra={})=>executeMockCheckpoint({path,intent:intent(),candidateContent:content,adapter,...extra});
test('persist then mock CAS and restart with no duplicate writes',()=>fixture(path=>{const adapter=createMockCheckpointAdapter(initial());assert.equal(run(path,adapter).status,'COMPLETE');assert.equal(adapter.writes(),1);assert.equal(run(path,adapter).status,'COMPLETE');assert.equal(adapter.writes(),1);}));
test('interruptions before and after CAS are recovered without repeating',()=>fixture(path=>{const adapter=createMockCheckpointAdapter(initial());assert.throws(()=>run(path,adapter,{beforeCas:()=>{throw Error('crash before');}}));assert.equal(adapter.writes(),0);assert.throws(()=>run(path,adapter,{afterCas:()=>{throw Error('crash after');}}));assert.equal(adapter.writes(),1);assert.equal(run(path,adapter).status,'COMPLETE');assert.equal(adapter.writes(),1);}));
test('changed ref, lock, or PR metadata before CAS prevents mutation',()=>{for(const [patch,status] of [[{state_sha:e},'CONFLICT'],[{lock:{status:'ACTIVE',owner:'foreign',run_id:'other'}},'FOREIGN_OR_UNEXPECTED_LOCK'],[{pr_head:e},'METADATA_LAG'],[{pr_base:e},'BASE_CHANGED']])fixture(path=>{const adapter=createMockCheckpointAdapter(initial());const r=run(path,adapter,{beforeCas:x=>x.change(patch)});assert.equal(r.status,status);assert.equal(adapter.writes(),0);});});
test('partial persisted checkpoint and invalid intent never cause a write',()=>fixture(path=>{const adapter=createMockCheckpointAdapter(initial());writeFileSync(path,'corrupt');assert.throws(()=>run(path,adapter));assert.equal(adapter.writes(),0);rmSync(path);assert.throws(()=>run(path,adapter,{candidateContent:'bad'}));assert.equal(adapter.writes(),0);adapter.change({state_sha:d,checkpoint_content:'partial'});assert.equal(run(path,adapter).status,'PARTIAL_CHECKPOINT');assert.equal(adapter.writes(),0);}));
test('mock adapter rejects arbitrary external adapter and force updates',()=>fixture(path=>{assert.throws(()=>run(path,{}),/mock adapter/);const adapter=createMockCheckpointAdapter(initial());assert.throws(()=>adapter.compareAndSwap({expected_sha:c,candidate_sha:d,expected_head:a,expected_base:b,content,force:true}));assert.equal(adapter.writes(),0);}));

test('atomic mock CAS checks every precondition again',()=>{for(const patch of [{state_sha:e},{branch_head:e},{pr_head:e},{pr_base:e},{lock:{status:'ACTIVE',owner:'foreign'}}]){const adapter=createMockCheckpointAdapter(initial());adapter.change(patch);assert.equal(adapter.compareAndSwap({expected_sha:c,candidate_sha:d,expected_head:a,expected_base:b,content,force:false}).status,'CONFLICT');assert.equal(adapter.writes(),0);}});
