/** Test-only native layout fixture. No service, credential or provider access. */
import React from 'react';
import { createRoot } from 'react-dom/client';
import { createBrowserRouter } from 'react-router';
import { App } from '../../frontend/src/app/App';
import { createBootstrap } from '../../frontend/src/app/config/publicSite';
import { createAppRoutes } from '../../frontend/src/app/routing/routes';
import { PageFrame } from '../../frontend/src/app/routing/pages';
import { StaffContext } from '../../frontend/src/features/staff/context';
import { ChatContext } from '../../frontend/src/features/chat/context';
import { ChatPage } from '../../frontend/src/features/chat/ChatPage';
import { CaseDraftContext } from '../../frontend/src/features/cases/context';
import { SubmissionContext } from '../../frontend/src/features/cases/submission';
import { CaseDraftPage } from '../../frontend/src/features/cases/CaseDraftPage';
import { CaseDetailContext } from '../../frontend/src/features/cases/detail-store';
import { CaseDetailPage } from '../../frontend/src/features/cases/CaseDetailPage';
import { KnowledgePanel } from '../../frontend/src/features/knowledge/KnowledgePage';
import { AdminPanel } from '../../frontend/src/features/admin/AdminPage';
import { SourceContext } from '../../frontend/src/features/source/store';
import { SourcePage } from '../../frontend/src/features/source/SourcePage';
import { makeStaffFixture, caseId } from '../../frontend/src/dev/staff-fixtures';
import { Component as ExtraPreview } from '../../frontend/src/dev/ExtraPreview';
import StaffCasesPage from '../../frontend/src/features/staff/StaffCasesPage';
import { fixture as chatFixture, ID } from '../../frontend/tests/chat/fixture';
import { submissionFixture, DRAFT } from '../../frontend/tests/cases/submission-fixture';
import { detailFixture, CASE } from '../../frontend/tests/cases/detail-fixture';
import { fixture as knowledgeFixture } from '../../frontend/tests/knowledge/fixture';
import { adminFixture } from '../../frontend/tests/extra/admin-fixture';
import { sourceFixture } from '../../frontend/tests/source/fixture';
import '../../frontend/src/styles/tokens.css';
import '../../frontend/src/styles/global.css';
const screen=new URLSearchParams(location.search).get('screen')||'home';
const longName='LAYOUT_UNBROKEN_'+ 'X'.repeat(480);
const long=('LAYOUT_LONG_SENTINEL '+ 'The delayed flight requires accessible service guidance. '.repeat(58)+longName).slice(0,3800);
let f:any, element:React.ReactNode, route='qa/layout';
if(screen==='chat') {
 f=chatFixture();
 f.server.events=[{id:CASE,sequence:1,event_type:'message_received',created_at:'2026-11-04T12:00:00Z',payload:{message_id:CASE,body:long,language:'en',author_type:'customer'}}];
 f.server.meta.last_sequence=1;
 await f.auth.start();
 route='qa/layout/:conversationId';
 element=<ChatContext value={f.store}><ChatPage /></ChatContext>;
} else if(screen==='review'||screen==='receipt'||screen==='unknown') {
 f=submissionFixture();f.previewServer.draft.payload.customer_report=long;await f.ready();
 if(screen==='receipt'){f.previewServer.draft.state='committed';f.previewServer.draft.result_ref='CASE-'+ '22222222222242228222222222222222';}
 if(screen==='unknown')f.submits.loseBeforeCommit=true;
 route='qa/layout/:draftId';
 element=<SubmissionContext value={f.submission}><CaseDraftContext value={f.cases}><CaseDraftPage /></CaseDraftContext></SubmissionContext>;
} else if(screen==='case') {
 f=detailFixture(); f.detailServer.detail.customer_report=long; f.detailServer.detail.resolution=long; await f.ready();
 route='qa/layout/:caseId'; element=<CaseDetailContext value={f.details}><CaseDetailPage /></CaseDetailContext>;
} else if(screen.startsWith('staff')) {
 f=makeStaffFixture();f.server.roles=['supervisor']; await f.ready();
 route='qa/layout/staff'; element=<StaffContext value={f.staff}><StaffCasesPage basePath="/qa/layout/staff" /></StaffContext>;
} else if(screen==='admin') {
 f=await adminFixture();f.access.member.display_name=longName;f.access.workspace.organization.name=longName;
 element=<StaffContext value={f.staff}><PageFrame staff><AdminPanel store={f.admin}/></PageFrame></StaffContext>;
} else if(screen==='knowledge') {
 f=await knowledgeFixture();f.state.document.title=longName.slice(0,200);f.state.version.body=long;
 element=<StaffContext value={f.staff}><PageFrame staff><KnowledgePanel store={f.store}/></PageFrame></StaffContext>;
} else if(screen==='rating'){f=chatFixture();await f.auth.start();element=<ExtraPreview />;
} else if(screen==='source') {
 f=sourceFixture(); f.source.booking.location=long;await f.ready(); await f.search();
 element=<SourceContext value={f.sources}><SourcePage /></SourceContext>;
} else { f=chatFixture();await f.auth.start(); }
const b=createBootstrap('localhost','');if(b.status!=='configured')throw Error('Test site unavailable');
const extra=element?[{path:route,element},...(screen.startsWith('staff')?[{path:route+'/:caseId',element}]:[])]:[];
const router=createBrowserRouter(createAppRoutes(b.site,undefined,extra,f.auth));
createRoot(document.getElementById('root')!).render(<App bootstrap={b} router={router}/>);
window.addEventListener('pagehide',()=>{f.dispose();router.dispose();},{once:true});
Object.assign(window,{layoutFixture:{screen,ID,DRAFT,CASE,caseId:caseId(1),controlled:true}});
