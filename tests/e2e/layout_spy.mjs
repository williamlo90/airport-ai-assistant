// Minimal call spy for reusing existing fixture factories outside a test runner.
export const vi={fn(implementation){let impl=implementation; const calls=[];
 const fn=(...args)=>{calls.push(args);return impl(...args);};
 fn.mock={calls};fn.getMockImplementation=()=>impl;
 fn.mockImplementation=(next)=>{impl=next;return fn;};return fn;}};
