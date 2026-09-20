// Run against tests/webui_fixture_server.py only; fixture counts are asserted.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
(async () => {
 const options = {headless: true};
 if (process.env.CHROMIUM_EXECUTABLE_PATH) options.executablePath = process.env.CHROMIUM_EXECUTABLE_PATH;
 const browser = await chromium.launch(options);
 const context = await browser.newContext({viewport:{width:1440,height:1050},acceptDownloads:true});
 const page = await context.newPage(); const errors=[];
 const base = process.env.PRISM_WEBUI_TEST_URL || 'http://127.0.0.1:8765';
 const screenshot = async name => {
  if (process.env.PRISM_SCREENSHOT_DIR) {
   await fs.mkdir(process.env.PRISM_SCREENSHOT_DIR, {recursive: true});
   await page.screenshot({path: require('node:path').join(process.env.PRISM_SCREENSHOT_DIR, name + '.png'), fullPage: true});
  }
 };
 page.on('pageerror', e=>errors.push(String(e)));
 page.on('console', msg=>{if(msg.type()==='error') errors.push(msg.text());});
 for (const path of ['/','/trading','/signals','/dry-run','/telegram','/queue','/activity','/readiness','/logs']) {
  const response=await page.goto(base+path); assert.equal(response.status(),200);
  assert.equal(await page.locator('h1').count(),1);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`overflow ${path}`);
 }
 await page.goto(base+'/'); assert.equal(await page.title(),'Command Center - PRISM');
 await page.selectOption('#console-theme','dark'); await screenshot('dashboard-dark');
 await page.selectOption('#console-theme','light'); await screenshot('dashboard-light');
 await page.reload(); assert.equal(await page.locator('html').getAttribute('data-theme'),'light');
 await page.keyboard.press('Control+k'); await page.locator('#command-query').fill('execution activity');
 assert.equal(await page.locator('.command-results a:visible').count(),1);
 await page.keyboard.press('Enter'); await page.waitForURL('**/activity');
 await page.goto(base+'/queue');
 assert.equal(await page.locator('tbody tr:visible').count(),25);
 await page.locator('[data-table-search]').fill('AAPL');
 assert.match(await page.locator('.table-footer').innerText(),/31 matching/);
 await page.locator('[data-table-filter="1"]').selectOption('failed');
 assert.equal(await page.locator('tbody tr:visible').count(),4);
 await page.locator('[data-table-filter="1"]').selectOption('');
 const downloadPromise=page.waitForEvent('download'); await page.locator('[data-export-csv]').click();
 const download=await downloadPromise; const downloadPath=await Promise.race([download.path(),new Promise((_,reject)=>setTimeout(()=>reject(new Error('download path timed out')),15000).unref())]); const csv=await fs.readFile(downloadPath,'utf8');
 assert.match(csv,/'=HYPERLINK/); assert.equal(csv.split('\r\n').length,32);
 await page.locator('[data-table-search]').fill('');
 await page.locator('th[data-sort-type="date"] button').first().click();
 assert.match(await page.locator('tbody tr:visible').first().innerText(),/2026-09-20T19:30:00-04:00/);
 await page.getByRole('button',{name:'Next',exact:true}).click(); assert.match(await page.locator('.table-footer').innerText(),/Page 2 of 3/);
 await page.selectOption('#console-theme','dark'); await screenshot('queue');
 await page.goto(base+'/signals');
 await page.selectOption('[data-example]','us-buy'); await page.getByRole('button',{name:'Validate signal',exact:true}).click();
 assert.equal(await page.getByRole('heading',{name:'Normalized signal'}).count(),1);
 await page.getByRole('button',{name:'Simulate this signal'}).click();
 assert.match(await page.locator('.result-panel').innerText(),/Simulation complete/i);
 assert.match(await page.locator('.result-panel').innerText(),/AAPL/);
 await page.setViewportSize({width:1024,height:768});
 for (const path of ['/', '/queue', '/activity']) {
  await page.goto(base+path);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`tablet overflow ${path}`);
 }
 await page.setViewportSize({width:390,height:844});
 for(const path of ['/','/queue','/activity','/signals','/readiness','/logs','/trading']) {
  await page.goto(base+path);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`mobile overflow ${path}`);
 }
 await page.goto(base+'/');
 assert.ok(await page.locator('.nav-item span').first().evaluate(el => el.getBoundingClientRect().width > 20), 'Mobile navigation labels are visible');
 await screenshot('mobile');
 const noJS=await browser.newContext({javaScriptEnabled:false});const fallback=await noJS.newPage(); await fallback.goto(base+'/queue');
 assert.equal(await fallback.locator('[data-table-tools]').isVisible(),false);
 assert.equal(await fallback.locator('tbody tr:visible').count(),62);
 assert.deepEqual(errors,[]);
 console.log('PASS: 9 routes, desktop/mobile overflow, persisted themes, command palette, filters, pagination, date sorting, CSV safety, simulation handoff, no-JS fallback; no browser errors.');
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
