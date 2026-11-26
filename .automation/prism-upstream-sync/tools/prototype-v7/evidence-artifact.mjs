// Offline artifact consistency checks only: caller-provided content is not authenticated.
import { createHash } from 'node:crypto';
const digest = x => createHash('sha256').update(x, 'utf8').digest('hex');
const sha1 = x => typeof x==='string' && /^[a-f0-9]{40}$/.test(x);
const sha256 = x => typeof x==='string' && /^[a-f0-9]{64}$/.test(x);
const nonempty = x => typeof x==='string' && x.trim().length>0;
const TESTS=Object.freeze([
  'validates state and rejects wrong sha, stale base, malformed inventory',
  'duplicate fails, HELD supersession requires explicit prior hash and new run',
  'self-asserted PASS including booleans and reused IDs is unsupported',
  'historical evidence is not current and held is revisitable',
  'CAS and append-only history, lock and metadata lag fail closed',
  'interrupted code execution classifies before, after, partial and complete, with stable restart',
  'checkpoint restart requires released lock and actual matching content',
  'stable restart and deterministic explicitly independent alternative after repeated stall',
  'actual synthetic git tree and tampered inventory',
  'CLI rejects merge and malformed or missing files without side effects',
]);
const specs=Object.freeze({
  'git-head-v1':Object.freeze({argv:Object.freeze(['git','rev-parse','HEAD']),kind:'head'}),
  'engine-tests-v1':Object.freeze({argv:Object.freeze(['node','--test','--test-reporter=tap','.automation/prism-upstream-sync/tools/prototype-v7/tests/maintenance-engine.test.mjs']),kind:'tap'}),
});
function fail(message){throw Error(message);}
export function specDigest(id) { if(!Object.hasOwn(specs,id)) fail('unknown spec'); return digest(JSON.stringify({id,argv:specs[id].argv,kind:specs[id].kind,tests:specs[id].kind==='tap'?TESTS:[]})); }
// Specs live in this source file, not in artifact input; no arbitrary command is executed.
export function validateArtifact(artifact, expected, history=[]) {
  if(!artifact || artifact.schema_version!==1 || !Object.hasOwn(specs,artifact.spec_id)) fail('unknown artifact schema or command spec');
  const spec=specs[artifact.spec_id];
  if (!sha256(expected?.spec_sha256) || expected.spec_sha256!==specDigest(artifact.spec_id) || artifact.spec_sha256!==expected.spec_sha256) fail('command spec pin mismatch');
  if(!Array.isArray(artifact.argv) || JSON.stringify(artifact.argv)!==JSON.stringify(spec.argv)) fail('command not allowlisted');
  if(!expected || !sha1(expected.head_sha) || !sha1(expected.base_sha) || artifact.head_sha!==expected.head_sha || artifact.base_sha!==expected.base_sha || !['R1','R2','F1','F2'].includes(artifact.round) || artifact.round!==expected.round) fail('artifact scope mismatch');
  if(!nonempty(artifact.execution_id) || !nonempty(artifact.run_id) || !nonempty(artifact.producer) || !Array.isArray(history)) fail('missing provenance');
  if(history.some(x=>x.execution_id===artifact.execution_id && (x.head_sha!==artifact.head_sha || x.base_sha!==artifact.base_sha || x.round!==artifact.round))) fail('execution ID reused across scopes');
  if(artifact.exit_code!==0 || typeof artifact.stdout!=='string' || Buffer.byteLength(artifact.stdout,'utf8')>1048576 || !sha256(artifact.stdout_sha256) || digest(artifact.stdout)!==artifact.stdout_sha256) fail('invalid captured output or digest');
  if(spec.kind==='head' && artifact.stdout!==`${expected.head_sha}\n`) fail('unexpected checkout output');
  if(spec.kind==='tap') {
    const lines=artifact.stdout.split(/\r?\n/);
    if(lines[0]!=='TAP version 13' || lines.some(x=>/^not ok\b/i.test(x) || /^ok\b.*#\s*(SKIP|TODO)\b/i.test(x))) fail('invalid or incomplete TAP execution');
    const tests=lines.filter(x=>/^ok \d+ - /.test(x));
    if(tests.length!==TESTS.length || tests.some((line,i)=>line!==`ok ${i+1} - ${TESTS[i]}`) || !lines.includes(`1..${TESTS.length}`) || !lines.includes(`# tests ${TESTS.length}`) || !lines.includes(`# pass ${TESTS.length}`) || !lines.includes('# fail 0') || lines.some(x=>/^# (?:cancelled|skipped|todo) [1-9]/.test(x))) fail('TAP assertions or summary mismatch');
  }
  return {status:'CONSISTENT_UNTRUSTED',spec_id:artifact.spec_id,execution_id:artifact.execution_id,content_sha256:artifact.stdout_sha256,certification:'NOT_GRANTED',next_exact_action:'Independently authenticate the producer and execution before using this artifact as evidence.'};
}
