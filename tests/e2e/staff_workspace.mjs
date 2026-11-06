/** Current production staff workspace against actual HTTPS/FastAPI/Neon. */
import { chromium } from '../../frontend/node_modules/playwright-core/index.mjs';
import { spawn } from 'node:child_process';
import { readFile,writeFile,unlink } from 'node:fs/promises';
import { openSync,closeSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';
import path from 'node:path';
import assert from 'node:assert/strict';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..'),out=path.join(root,'local/team/fe-qa-04'),file=n=>path.join(out,n),wait=ms=>new Promise(r=>setTimeout(r,ms));
const report={passed:false,checks:[],browser_errors:[],uncontrolled_requests:0,provider_calls:0,emails_sent:0};
for(const n of['ready.json','stop','control.json','server-error.json','server-result.json','cleanup.json','target.json','http-failure.json'])await unlink(file(n)).catch(()=>{});
const log=openSync(file('server.log'),'w'),server=spawn(path.join(root,'backend/.venv/Scripts/python.exe'),[path.join(root,'tests/e2e/staff_workspace_server.py')],{cwd:root,windowsHide:true,stdio:['ignore',log,log]}),exited=new Promise(resolve=>server.once('exit',resolve));
let browser,context,page,ready,stage='startup';
const check=(name,extra={})=>report.checks.push({name,...extra});
const readWhen=async(n)=>{for(let i=0;i<600;i++){try{return JSON.parse(await readFile(file(n),'utf8'));}catch{}if(server.exitCode!==null)throw Error('Isolated server exited before '+n);await wait(250);}throw Error('Bounded artifact wait '+n);};
const control=async(action)=>{const id=randomUUID();await writeFile(file('control.json'),JSON.stringify({action,id}));return readWhen(id+'.json');};
try{
 ready=await readWhen('ready.json');await unlink(file('ready.json'));report.schema=ready.schema;
 browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,args:['--disable-background-networking','--disable-component-update','--no-first-run','--renderer-process-limit=2']});
 context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1440,height:950},serviceWorkers:'block'});
 const origin=new URL(ready.origin).origin;let assignmentPatches=0,statusPatches=0,dropAssignment=false;const assignStatuses=[];
 await context.route('**/*',async(route)=>{
  const req=route.request(),u=new URL(req.url());if(u.origin!==origin){report.uncontrolled_requests++;await route.abort();return;}
  if(req.method()==='PATCH'&&u.pathname.endsWith('/assignment')){
   assignmentPatches++;
   if(dropAssignment){dropAssignment=false;const actual=await route.fetch();assignStatuses.push(actual.status());assert.equal(actual.status(),200);const body=await actual.json();assert.equal(body.assigned_membership_id,ready.membership);await route.abort('failed');return;}
  }
  if(req.method()==='PATCH'&&u.pathname.endsWith('/status'))statusPatches++;
  await route.continue();
 });
 page=await context.newPage();page.setDefaultTimeout(30000);page.on('pageerror',e=>report.browser_errors.push(e.name));
 stage='normal_staff_login';await page.goto(origin+'/staff/login?lang=en');await page.getByRole('button',{name:'Prepare staff sign-in',exact:true}).click();await page.getByLabel('Staff email',{exact:true}).fill(ready.contact);await page.getByLabel('Password',{exact:true}).fill(ready.password);
 const signed=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/v1/staff/auth/login'&&r.status()===200);await page.getByRole('button',{name:'Sign in',exact:true}).click();await signed;
 await page.goto(origin+'/staff/cases?lang=en');await page.getByLabel('Organization access',{exact:true}).selectOption(ready.membership);await page.getByRole('button',{name:'All',exact:true}).waitFor();check('normal_staff_login_and_explicit_current_membership_selection');
 stage='pagination';const seen=[];let pages=0,watermark;
 async function next(action){const response=page.waitForResponse(r=>{const u=new URL(r.url());return u.pathname==='/api/v1/staff/queues/cases'&&u.searchParams.get('membership_id')===ready.membership&&u.searchParams.get('view')==='all'&&r.status()===200;});await action();const data=await(await response).json();if(data.items.length)await page.locator('a[href^="/staff/cases/'+data.items[0].id+'"]').waitFor();assert(!('total' in data));watermark??=data.watermark;assert.equal(data.watermark,watermark);seen.push(...data.items.map(x=>x.id));pages++;return data;}
 let data=await next(()=>page.getByRole('button',{name:'All',exact:true}).click());while(data.has_more)data=await next(()=>page.getByRole('button',{name:'Next page',exact:true}).click());
 assert.deepEqual(seen,ready.expected_ids);assert.equal(new Set(seen).size,106);assert.equal(pages,5);check('native_real106_case_cursor_pagination_25_five_pages_exact_no_duplicate_or_invented_total',{cases:106,pages});
 await page.locator('a[href^="/staff/cases/'+ready.case_id+'"]').click();await page.getByRole('heading',{name:'Customer report',exact:true}).waitFor();assert(!await page.getByText('FOREIGN_REPORT_MUST_STAY_HIDDEN',{exact:true}).count());
 stage='assignment_review';await page.getByRole('button',{name:'Find eligible staff',exact:true}).click();await page.getByLabel('Eligible staff',{exact:true}).selectOption(ready.membership);await page.getByRole('button',{name:'Review assignment',exact:true}).click();await page.getByRole('dialog',{name:'Confirm assignment change',exact:true}).waitFor();await page.getByRole('button',{name:'Keep current assignment',exact:true}).click();assert.equal(assignmentPatches,0);
 await page.getByRole('button',{name:'Review assignment',exact:true}).click();assert((await control('bump')).assigned_other);
 const conflict=page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/assignment')&&r.request().method()==='PATCH'&&r.status()===409);await page.getByRole('button',{name:'Confirm assignment',exact:true}).click();await conflict;
 await page.getByText(/Review the current case and permissions before submitting again/).waitFor();assert.equal(assignmentPatches,1);await page.getByRole('button',{name:'I have reviewed the current case',exact:true}).click();check('native_stale_assignment_review_receives_real409_and_requires_fresh_review_without_replay');
 stage='lost_assignment_ack';await page.getByRole('button',{name:'Find eligible staff',exact:true}).click();await page.getByLabel('Eligible staff',{exact:true}).selectOption(ready.membership);await page.getByRole('button',{name:'Review reassignment',exact:true}).click();dropAssignment=true;await page.getByRole('button',{name:'Confirm assignment',exact:true}).click();await page.getByText(/The request result is not confirmed. Review the latest case and history/).waitFor();assert.equal(assignmentPatches,2);assert.deepEqual(assignStatuses,[200]);check('actual_assignment_PATCH200_committed_then_dropped_GET_stays_uncertain_no_replay',{browser_assignment_patches:2,successful_dropped:1});
 await page.getByRole('button',{name:'I have reviewed the current case',exact:true}).click();
 stage='duplicate_denial';await page.getByLabel('Closure reason',{exact:true}).selectOption('duplicate');await page.getByLabel('Original case reference',{exact:true}).fill(ready.foreign_ref);await page.getByRole('button',{name:'Find original case',exact:true}).click();await page.getByText('No eligible case found for this reference.',{exact:true}).waitFor();assert(!await page.getByText('FOREIGN_REPORT_MUST_STAY_HIDDEN',{exact:true}).count());check('native_unauthorized_duplicate_reference_returns_same_generic_empty_without_target_report');
 stage='no_response_closure';await page.getByLabel('Closure reason',{exact:true}).selectOption('no_response');await page.getByLabel('Outcome for the customer',{exact:true}).fill('No further response was received for this synthetic test.');await page.getByLabel('Next step after closure',{exact:true}).fill('Contact support if the same issue continues.');await page.getByRole('button',{name:'Review closure',exact:true}).click();assert.equal(await page.getByRole('dialog',{name:'Confirm case closure',exact:true}).count(),0);assert.equal(statusPatches,0);
 await page.getByLabel('Follow-up evidence',{exact:true}).fill('Synthetic follow-up attempt recorded for this test; no email delivery claimed.');await page.getByRole('button',{name:'Review closure',exact:true}).click();await page.getByRole('dialog',{name:'Confirm case closure',exact:true}).waitFor();await page.getByRole('button',{name:'Keep case open',exact:true}).click();assert.equal(statusPatches,0);
 await page.getByRole('button',{name:'Review closure',exact:true}).click();await page.getByRole('button',{name:'Close case',exact:true}).click();await page.waitForFunction(()=>!document.querySelector('#close-reason'));await page.getByText('No further response was received for this synthetic test.',{exact:true}).first().waitFor();assert.equal(statusPatches,1);check('native_no_response_requires_explicit_evidence_cancellable_review_then_one_real_close_and_readonly_controls');
 stage='reload_closed';await page.reload();await page.getByLabel('Organization access',{exact:true}).selectOption(ready.membership);await page.getByRole('button',{name:'All',exact:true}).click();await page.getByLabel('Status',{exact:true}).selectOption('closed');await page.locator('a[href^="/staff/cases/'+ready.case_id+'"]').click();await page.getByRole('heading',{name:'Customer report',exact:true}).waitFor();await page.getByText('No further response was received for this synthetic test.',{exact:true}).first().waitFor();assert.equal(await page.getByLabel('Closure reason',{exact:true}).count(),0);assert.equal(assignmentPatches,2);assert.equal(statusPatches,1);check('native_reload_reselects_membership_and_preserves_actual_closed_history_without_more_mutations');
 assert((await control('verify')).verified);
 stage='revocation';assert((await control('revoke')).revoked);await page.getByRole('button',{name:'Refresh staff access',exact:true}).click();await page.waitForFunction(()=>!document.body.textContent.includes('WORKSPACE_MAIN_REPORT'));assert.equal(await page.getByRole('heading',{name:'Customer report',exact:true}).count(),0);assert.equal(await page.getByLabel('Organization access',{exact:true}).inputValue(),ready.other_membership);check('actual_membership_revocation_purges_private_workspace_and_admin_other_org_does_not_inherit_CS');
 assert.deepEqual(report.browser_errors,[]);assert.equal(report.uncontrolled_requests,0);assert(await page.evaluate(()=>localStorage.length===0&&sessionStorage.length===0));report.passed=true;
}catch(error){report.failure={type:error.name,stage,message:String(error.message).replaceAll(ready?.password??'__no_password__','[redacted]').replace(/\b\d{8}\b/g,'[redacted]').slice(0,170)};process.exitCode=1;}
finally{
 if(context){await context.close();report.context_closed=true;}if(browser){await browser.close();report.browser_closed=true;}await writeFile(file('stop'),'stop');
 const code=await Promise.race([exited,new Promise(resolve=>{const timer=setTimeout(()=>resolve(null),45000);timer.unref();})]);if(code===null){server.kill();await exited;report.server_forced_stop=true;process.exitCode=1;}
 report.server_exit=server.exitCode;closeSync(log);report.passed=report.passed&&server.exitCode===0&&!report.server_forced_stop;if(!report.passed)process.exitCode=1;
 await writeFile(file('browser-result.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({passed:report.passed,checks:report.checks.length,server_exit:report.server_exit,failure:report.failure??null}));
}
