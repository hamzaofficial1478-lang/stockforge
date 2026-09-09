// Run with: node --test tests/ui_queue.test.cjs (no npm dependencies).
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../stockforge/ui/app.html'), 'utf8');
const script = html.match(/<script>([\s\S]*)<\/script>/)[1];

function panel(fetch){
  const elements = Object.fromEntries([...html.matchAll(/\bid="([^"]+)"/g)].map(([,id]) => [id, {
    value: '', textContent: '', innerHTML: '', hidden: false, disabled: false,
    style: {}, classList: {toggle(){}}, addEventListener(){}, children: [],
  }]));
  const context = vm.createContext({fetch, AbortSignal, console, setTimeout(){},
    document: {getElementById: id => elements[id] || null},
  });
  // Load the actual handlers without starting automatic networking.
  vm.runInContext(script.slice(0, script.lastIndexOf('\nshowDoor();')), context);
  return {elements, run: code => vm.runInContext(code, context)};
}
const idle = {state:'idle', done:0, review:0, failed:0, remaining:1, recent:[]};

test('Start reaches the server using the elements in the real HTML', async () => {
  const requests = [];
  const p = panel(async (url, options) => {
    requests.push([url, options]);
    return {ok:true, json:async () => url.startsWith('/api/designs') ? [] : {...idle, state:'running', current_step:'Starting'}};
  });
  await p.run("worker('start')");
  assert.equal(requests[0][0], '/api/worker');
  assert.deepEqual(JSON.parse(requests[0][1].body), {action:'start'});
  assert.equal(p.elements['w-start'].disabled, true);
  assert.match(p.elements['worker-line'].textContent, /running/);
});

test('A failed start request shows a visible alert', async () => {
  const p = panel(async () => {throw new Error('offline');});
  await p.run("worker('start')");
  assert.equal(p.elements['worker-alert'].hidden, false);
  assert.match(p.elements['worker-alert'].textContent, /Could not reach Stockforge/);
});

test('Polling refreshes the design table after the worker finishes', async () => {
  const requests = [];
  const p = panel(async url => {
    requests.push(url);
    return {ok:true, json:async () => url.startsWith('/api/designs') ? [{id:'one', state:'master_only', image_count:1, files:[]}] : {...idle, remaining:0, done:1}};
  });
  await p.run("active = 'queue'; pollWorker()");
  assert.ok(requests.some(url => url.startsWith('/api/designs')));
  assert.match(p.elements['design-table'].innerHTML, /master_only/);
});

test('Lost connection is visible and polling can recover', async () => {
  let offline = true;
  const p = panel(async () => {
    if (offline) throw new Error('offline');
    return {ok:true, json:async () => idle};
  });
  await p.run('pollWorker()');
  assert.match(p.elements['worker-detail'].textContent, /Connection lost/);
  offline = false;
  await p.run('pollWorker()');
  assert.equal(p.elements['worker-alert'].hidden, true);
  assert.match(p.elements['worker-detail'].textContent, /Connected/);
});
