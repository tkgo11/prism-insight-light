"""Serve isolated synthetic UI data for the optional browser smoke test.

Never reads deployment configuration or sends broker requests.
"""
import json, os, sys, tempfile, shutil
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
runtime = Path(tempfile.mkdtemp(prefix='prism-webui-fixture-'))
os.environ['PRISM_RUNTIME_DIR']=str(runtime)
os.environ['PRISM_KIS_CONFIG_PATH']=str(runtime/'config.yaml')
(runtime/'config.yaml').write_text('default_mode: demo\nauto_trading: false\naccounts: []\n')
queue=[]
for i in range(62):
 queue.append({'status':'failed' if i%7==0 else 'pending', 'execute_at': '2026-09-21T09:00:00+09:00' if i%2==0 else '2026-09-20T19:30:00-04:00', 'created_at':'2026-09-19T12:00:00Z', 'failure_message':'Fixture failure: inspect log' if i%7==0 else '', 'signal':{'type':'BUY' if i%3 else 'SELL', 'market':'KR' if i%2==0 else 'US', 'ticker':'005930' if i%2==0 else 'AAPL','company_name':'Samsung Electronics' if i%2==0 else 'Apple'}})
queue[1]['signal']['company_name']='=HYPERLINK("https://example.invalid","unsafe")'
(runtime/'off_hours_queue.json').write_text(json.dumps(queue))
(runtime/'multi_account_execution_ledger.json').write_text(json.dumps({f'{i:064x}':{'status':['executed','unknown','in_progress','failed'][i%4], 'claimed_at':f'2026-09-19T12:{i%60:02d}:00Z'} for i in range(62)}))
from webui.app import create_app, WebUISettings
app=create_app(WebUISettings(force_dry_run=True))
import uvicorn
try:
    uvicorn.run(app, host='127.0.0.1', port=int(os.environ.get('PRISM_WEBUI_TEST_PORT', '8765')))
finally:
    shutil.rmtree(runtime)
