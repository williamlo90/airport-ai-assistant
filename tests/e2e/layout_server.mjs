import {createServer} from '../../frontend/node_modules/vite/dist/node/index.js';
import react from '../../frontend/node_modules/@vitejs/plugin-react/dist/index.js';
import {writeFile} from 'node:fs/promises';
import path from 'node:path';import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const dependencies=['react','react-dom','react-router','zod','react-hook-form','@hookform/resolvers'];
const aliases=dependencies.map(name=>({find:new RegExp('^'+name.replace('/','\\/')+'(?=/|$)'),replacement:path.join(root,'frontend/node_modules',name)}));
aliases.push({find:'vitest',replacement:path.join(root,'tests/e2e/layout_spy.mjs')});
const entry='/@fs/'+path.join(root,'tests/e2e/layout_fixture.tsx').replaceAll('\\','/');
const server=await createServer({root:path.join(root,'frontend'),configFile:false,envFile:false,plugins:[react(),{name:'layout-test-html',configureServer(s){s.middlewares.use(async(req,res,next)=>{if(!req.headers.accept?.includes('text/html'))return next();const html=await s.transformIndexHtml(req.url,'<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="root"></div><script type="module" src="'+entry+'"></script></body></html>');res.setHeader('Content-Type','text/html');res.end(html);});}}],resolve:{alias:aliases,dedupe:dependencies},optimizeDeps:{noDiscovery:true,include:[...dependencies,"react-dom/client","react/jsx-runtime","react/jsx-dev-runtime","@hookform/resolvers/zod"]},server:{host:'127.0.0.1',port:19475,strictPort:true,fs:{allow:[root]}},logLevel:'error'});
await server.listen();console.log('LAYOUT_SERVER_READY');if(process.env.LAYOUT_RUN_ID)await writeFile(path.join(root,'local/team/fe-qa-05/server-ready.json'),JSON.stringify({run_id:process.env.LAYOUT_RUN_ID,pid:process.pid}));
let closing=false;async function stop(){if(closing)return;closing=true;await server.close();process.exit(0);}process.on('SIGTERM',stop);process.on('SIGINT',stop);

process.stdin.on('data',data=>{if(data.toString().trim()==='stop')void stop();});
