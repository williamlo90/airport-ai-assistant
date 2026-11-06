/** Native customer UI -> actual FastAPI/Neon. Only committed ACKs are deliberately dropped. */
import { chromium } from '../../frontend/node_modules/playwright-core/index.mjs';
import { spawn } from 'node:child_process';
import { readFile,writeFile,unlink } from 'node:fs/promises';
import { openSync,closeSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import assert from 'node:assert/strict';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..'),out=path.join(root,'local/team/fe-qa-03');
const file=name=>path.join(out,name),wait=ms=>new Promise(r=>setTimeout(r,ms));
const report={passed:false,checks:[],browser_errors:[],uncontrolled_requests:0,provider_calls:0,emails_sent:0};
for(const name of['ready.json','stop','otp.json','control.json','closed.json','verified.json','server-result.json','server-error.json','cleanup.json','http-failure.json','target.json'])await unlink(file(name)).catch(()=>{});
const log=openSync(file('server.log'),'w');
const server=spawn(path.join(root,'backend/.venv/Scripts/python.exe'),[path.join(root,'tests/e2e/customer_recovery_server.py')],{cwd:root,windowsHide:true,stdio:['ignore',log,log]});
const exited=new Promise(resolve=>server.once('exit',resolve));
let browser,context,page;
const check=(name,extra={})=>report.checks.push({name,...extra});
const readWhen=async(name)=>{for(let i=0;i<600;i++){try{return JSON.parse(await readFile(file(name),'utf8'));}catch{}if(server.exitCode!==null)throw Error('Isolated server exited before '+name);await wait(250);}throw Error('Artifact wait deadline '+name);};
try{
 const ready=await readWhen('ready.json');report.schema=ready.schema;
 browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,args:['--disable-background-networking','--disable-component-update','--no-first-run','--renderer-process-limit=2']});
 context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1200,height:900},serviceWorkers:'block'});
 const origin=new URL(ready.origin).origin,requests=[];let confirmPosts=0,continuationPosts=0,draftCreates=0,draftId,caseId,resultRef,dropConfirm=true,dropContinue=true;
 await context.route('**/*',async(route)=>{
  const req=route.request(),u=new URL(req.url());
  if(u.origin!==origin){report.uncontrolled_requests++;await route.abort();return;}
  if(u.pathname.startsWith('/api/'))requests.push({path:u.pathname,method:req.method()});
  if(req.method()==='POST'&&u.pathname==='/api/v1/action-drafts')draftCreates++;
  if(req.method()==='POST'&&u.pathname.endsWith('/confirm')){
   confirmPosts++;
   if(dropConfirm){dropConfirm=false;const actual=await route.fetch();assert.equal(actual.status(),201);const receipt=await actual.json();caseId=receipt.case_id;resultRef=receipt.result_ref;assert.equal(receipt.state,'committed');await route.abort('failed');return;}
  }
  if(req.method()==='POST'&&u.pathname.endsWith('/continue')){
   continuationPosts++;
   if(dropContinue){dropContinue=false;const actual=await route.fetch();assert.equal(actual.status(),200);const actualCase=await actual.json();assert.equal(actualCase.id,caseId);assert.equal(actualCase.status,'in_progress');await route.abort('failed');return;}
  }
  await route.continue();
 });
 page=await context.newPage();page.setDefaultTimeout(30000);page.on('pageerror',e=>report.browser_errors.push(e.name));
 await page.goto(origin+'/help/new?topic=contact&lang=en');
 await page.getByRole('button',{name:'Start conversation',exact:true}).click();
 await page.getByLabel('Your message',{exact:true}).fill('Please explain where I should ask for airport information.');
 await page.getByRole('button',{name:'Save message',exact:true}).click();await page.getByText('Saved',{exact:true}).first().waitFor();
 await page.getByRole('link',{name:'Continue to email verification',exact:true}).click();
 await page.getByLabel('Email address',{exact:true}).fill('customer@example.com');await page.getByRole('button',{name:'Request a code',exact:true}).click();
 await page.getByLabel('8-digit code',{exact:true}).waitFor();const otp=await readWhen('otp.json');await unlink(file('otp.json'));
 await page.getByLabel('8-digit code',{exact:true}).fill(otp.code);await page.getByRole('button',{name:'Verify and continue',exact:true}).click();await page.getByRole('button',{name:'Continue with reviewed report',exact:true}).click();
 await page.getByRole('button',{name:'Save message',exact:true}).click();await page.getByText('Saved',{exact:true}).first().waitFor();
 check('normal_browser_bootstrap_otp_adoption_and_current_customer_session');
 await page.getByRole('button',{name:'Review support request',exact:true}).click();await page.getByLabel('Short summary',{exact:true}).fill('Airport information assistance');
 await page.getByLabel('What do you need help with?',{exact:true}).selectOption('information');await page.getByLabel('Service',{exact:true}).selectOption('general');
 await page.getByLabel('Your report',{exact:true}).fill('Please explain where to ask for airport information.');await page.getByLabel('Email for follow-up',{exact:true}).fill('customer@example.com');
 await page.getByRole('button',{name:'Prepare review',exact:true}).click();await page.getByRole('button',{name:'Send support request',exact:true}).waitFor();
 draftId=new URL(page.url()).pathname.split('/').at(-1);assert.match(draftId,/^[a-f0-9-]{36}$/);const pointer=page.url();assert.equal(draftCreates,1);assert.equal(confirmPosts,0);
 await page.reload();await page.getByRole('button',{name:'Send support request',exact:true}).waitFor();assert.equal(confirmPosts,0);assert.equal(draftCreates,1);check('pending_hard_reload_gets_existing_preview_without_new_draft_or_confirmation');
 await page.getByRole('button',{name:'Send support request',exact:true}).dblclick();await page.getByRole('heading',{name:'Your support request is recorded',exact:true}).waitFor();assert.equal(confirmPosts,1);assert.equal(draftCreates,1);assert(resultRef&&caseId);
 await page.getByText(resultRef,{exact:true}).waitFor();check('actual_committed201_dropped_then_GET_receipt_no_resend_or_doubleclick_duplicate',{confirm_posts:confirmPosts});
 await page.reload();await page.getByText(resultRef,{exact:true}).waitFor();assert.equal(confirmPosts,1);assert.equal(draftCreates,1);check('committed_hard_reload_recovers_identical_server_receipt_without_replaying_consent');
 await page.getByRole('link',{name:'View case details',exact:true}).click();await page.getByRole('heading',{name:'Your case, at a glance',exact:true}).waitFor();
 await writeFile(file('control.json'),JSON.stringify({action:'close',case_id:caseId,draft_id:draftId}));const closed=await readWhen('closed.json');assert(closed.closed&&closed.assigned);
 await page.getByRole('button',{name:'Refresh case',exact:true}).click();await page.getByRole('button',{name:'My issue is not resolved',exact:true}).click();
 await page.getByLabel('Your explanation',{exact:true}).fill('The same airport information issue still needs help.');await page.getByRole('checkbox').check();await page.getByRole('button',{name:'Request further handling',exact:true}).click();
 await page.getByRole('heading',{name:'Your follow-up result is not confirmed',exact:true}).waitFor();assert.equal(continuationPosts,1);
 assert.equal(await page.getByText('Your follow-up request was recorded. The current case status is shown above.',{exact:true}).count(),0);
 await page.getByRole('button',{name:'Check latest case',exact:true}).click();await page.getByRole('heading',{name:'Your follow-up result is not confirmed',exact:true}).waitFor();assert.equal(continuationPosts,1);
 assert.equal(await page.getByRole('button',{name:'Retry the same request',exact:true}).count(),0);assert.equal(await page.getByRole('button',{name:'Request further handling',exact:true}).count(),0);
 check('actual_continuation200_dropped_GET_current_active_state_remains_unknown_no_false_ack_no_replay',{continuation_posts:continuationPosts});
 await page.reload();await page.getByRole('heading',{name:'Previous handling results',exact:true}).waitFor();await page.getByText('The same airport information issue still needs help.',{exact:true}).waitFor();assert.equal(continuationPosts,1);
 assert.equal(await page.getByText('Your follow-up request was recorded. The current case status is shown above.',{exact:true}).count(),0);check('reload_preserves_real_closure_and_reopen_history_without_claiming_own_success');
 await writeFile(file('control.json'),JSON.stringify({action:'verify',case_id:caseId,draft_id:draftId}));assert((await readWhen('verified.json')).verified);
 const start=requests.length;await page.goto(origin+'/help/submissions?lang=en');await page.getByText(/original request link is missing/).waitFor();
 const after=requests.slice(start);assert(after.every(r=>r.method==='GET'&&!/\/(actions|action-drafts|cases)\//.test(r.path)));assert.equal(confirmPosts,1);assert.equal(draftCreates,1);check('missing_draft_pointer_does_not_lookup_invent_or_create_replacement');
 const privateState=await page.evaluate(()=>({emptyStorage:localStorage.length===0&&sessionStorage.length===0,noContactInURL:!location.href.includes('customer%40')&&!location.href.includes('customer@'),httpOnlyHidden:!document.cookie.includes('__Host-airport_session')}));assert(Object.values(privateState).every(Boolean));assert.equal(report.uncontrolled_requests,0);assert.deepEqual(report.browser_errors,[]);
 check('no_contact_in_URL_no_browser_storage_and_no_browser_error');report.passed=true;
}catch(error){report.failure={type:error.name,message:String(error.message).replace(/\b\d{8}\b/g,'[redacted]').slice(0,170)};process.exitCode=1;}
finally{
 if(context){await context.close();report.context_closed=true;}if(browser){await browser.close();report.browser_closed=true;}
 await writeFile(file('stop'),'stop');
 const code=await Promise.race([exited,new Promise(resolve=>{const timer=setTimeout(()=>resolve(null),45000);timer.unref();})]);
 if(code===null){server.kill();await exited;report.server_forced_stop=true;process.exitCode=1;}
 report.server_exit=server.exitCode;closeSync(log);report.passed=report.passed&&server.exitCode===0&&!report.server_forced_stop;
 if(!report.passed)process.exitCode=1;
 await writeFile(file('browser-result.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({passed:report.passed,checks:report.checks.length,server_exit:report.server_exit,failure:report.failure??null}));
}
