// THROWAWAY PROTOTYPE: in-memory interaction only. Nothing is production code.

const labels = {
  a: "A — Operations console",
  b: "B — Intent launcher",
  c: "C — Resource graph",
};

const fixture = `
  <div class="fixture">
    <span class="ok">● Supervisor running</span>
    <span class="ok">● Gateway ready</span>
    <code>127.0.0.1:8766/v1</code>
  </div>`;

const serviceDetail = (prefix) => `
  <section class="scene" id="${prefix}-service">
    <button class="back" data-scene="${prefix}-home">← Back</button>
    <div class="heading"><div><p class="eyebrow">INFERENCE SERVICE</p><h1><i class="stopped"></i> coding</h1><p>Desired service is stopped because launch validation failed.</p></div><div><button>Edit</button> <button class="primary" disabled>▶ Start</button> <button class="danger">Remove…</button></div></div>
    <div class="tabs"><button class="selected">Summary</button><button>Runs</button><button>Logs</button><button>Metrics</button><button>Configuration</button></div>
    <div class="detail-grid">
      <article class="card"><p class="eyebrow">DESIRED STATE</p><dl><dt>Model Alias</dt><dd>qwen-optiq</dd><dt>Revision</dt><dd><code>70a3aa32c7fe…</code></dd><dt>Runtime</dt><dd>optiq@0.2.15</dd><dt>Gateway Route</dt><dd><code>coding</code></dd><dt>Activation</dt><dd>Manual</dd></dl></article>
      <article class="card"><p class="eyebrow">LATEST SERVICE RUN</p><div class="event bad">× Rejected before launch</div><dl><dt>Run ID</dt><dd><code>run_coding_latest</code></dd><dt>Process</dt><dd>Not created</dd><dt>Upstream</dt><dd>Not allocated</dd><dt>Metrics</dt><dd>No samples</dd></dl><button data-scene="${prefix}-doctor">Explain and repair →</button></article>
      <article class="card wide"><p class="eyebrow">LATEST LOG EVENTS</p><div class="log">supervisor&nbsp; validating service coding<br>runtime&nbsp;&nbsp;&nbsp;&nbsp; optiq@0.2.15 capabilities loaded<br><span>error&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; option --max-context is unsupported</span><br>supervisor&nbsp; launch aborted; no process created</div><p>No performance data exists for this run.</p></article>
    </div>
  </section>`;

const firstRun = (prefix) => `
  <section class="scene" id="${prefix}-first">
    <button class="back" data-scene="${prefix}-home">← Back</button>
    <div class="heading"><div><p class="eyebrow">FIRST RUN · STEP 3 OF 5</p><h1>Create your first service</h1><p>Nothing starts until the final plan is reviewed.</p></div></div>
    <div class="wizard">
      <ol><li class="done">1 Supervisor <small>Running</small></li><li class="done">2 Runtime <small>OptiQ inspected</small></li><li class="now">3 Model <small>Select exact revision</small></li><li>4 Service <small>Name and route</small></li><li>5 Verify <small>Preview and test</small></li></ol>
      <article class="card"><p class="eyebrow">MODEL CATALOG</p><h2>What should this service run?</h2><div class="actions"><button class="primary">Browse mlx-community</button><button>Search all Hugging Face</button><button>Choose local path</button></div><button class="model-choice"><i>●</i><span><strong>mlx-community/Qwen3.6-35B-A3B-OptiQ-4bit</strong><small>Exact revision 70a3aa32c7fe… · snapshot cached</small></span><em>CONFLICTING</em></button><div class="evidence"><span>Availability <b>complete</b></span><span>Fit <b>unknown</b></span><span>Compatibility <b class="yellow">conflicting</b></span><span>Trust <b>revision pinned</b></span></div><article class="inline-alert"><strong>Setup paused safely</strong><p>Repair or replace the Runtime Installation so the launch options are advertised.</p><button data-scene="${prefix}-doctor">Open Doctor</button></article></article>
    </div>
  </section>`;

const doctor = (prefix) => `
  <section class="scene" id="${prefix}-doctor">
    <button class="back" data-scene="${prefix}-home">← Back</button>
    <div class="heading"><div><p class="eyebrow">DOCTOR</p><h1>Upgrade OptiQ without breaking <code>coding</code></h1><p>Checks are read-only. Every change is previewed.</p></div><button>Copy diagnostic</button></div>
    <article class="repair"><em>BLOCKING</em><div><h2>Launch options do not match optiq@0.2.15</h2><p>Install a tested compatible Runtime Installation side by side, probe it, validate the service, then switch.</p><div class="repair-steps"><span>1 Resolve tested definition</span><span>2 Install alongside</span><span>3 Probe capabilities</span><span>4 Dry-run coding</span><span>5 Switch after validation</span></div><small>Model cache unchanged · Gateway remains ready · Current runtime retained · Configuration backed up atomically</small></div><button class="primary">Begin durable repair…</button></article>
  </section>`;

function consoleVariant() {
  return `
    <main class="variant console">
      <header class="topbar"><div class="brand">◈ <strong>mlxctl</strong><small> local inference console</small></div>${fixture}<button class="palette-trigger">⌘ K Commands</button></header>
      <div class="console-shell">
        <nav class="resource-nav"><small>OPERATE</small><button class="selected" data-scene="a-home">⌂ Overview</button><button data-scene="a-service">◆ Services <b>1</b></button><button data-scene="a-service">↻ Runs <b>1</b></button><small>SUPPLY</small><button data-scene="a-library">⬡ Runtimes <b>3</b></button><button data-scene="a-library">▱ Model library <b>1</b></button><button data-scene="a-library">⌕ Catalog</button><button data-scene="a-library">◫ Cache</button><small>SYSTEM</small><button data-scene="a-system">⇄ Gateway</button><button data-scene="a-system">◎ Supervisor</button><button data-scene="a-ops">⌁ Logs &amp; metrics</button><button data-scene="a-system">⚙ Configuration</button><button class="attention" data-scene="a-doctor">✚ Doctor <b>1</b></button></nav>
        <section class="workspace">
          <section class="scene active" id="a-home"><div class="heading"><div><p class="eyebrow">OPERATIONS</p><h1>Good morning, Ivan.</h1><p>One service needs attention before it can run.</p></div><button class="primary" data-scene="a-first">＋ Create service</button></div><article class="alert"><strong>! coding cannot start</strong><p>OptiQ 0.2.15 does not advertise <code>--max-context</code>.</p><button data-scene="a-doctor">Review repair</button></article><div class="card-grid"><article class="card wide"><p class="eyebrow">INFERENCE SERVICES</p><h2>Services</h2><button class="service-row" data-scene="a-service"><i class="stopped"></i><span><strong>coding</strong><small>qwen-optiq · optiq@0.2.15</small></span><code>route: coding</code><em>BLOCKED</em><b>›</b></button></article><article class="card"><p class="eyebrow">FIRST USE</p><h2>First useful response</h2><ol class="checklist"><li class="done">Supervisor ready</li><li class="done">Runtime inspected</li><li class="now">Choose exact model revision</li><li>Create named service</li><li>Send test request</li></ol><button data-scene="a-first">Continue setup →</button></article><article class="card"><p class="eyebrow">RECENT ACTIVITY</p><h2>Operations</h2><div class="event ok">✓ Gateway ready</div><div class="event bad">× coding run rejected</div><div class="empty">No active jobs or request metrics.</div></article></div></section>
          ${firstRun("a")}${serviceDetail("a")}${doctor("a")}
          <section class="scene" id="a-library"><button class="back" data-scene="a-home">← Overview</button><h1>Runtimes, models, catalog, and cache</h1><p>Desired installations stay distinct from shared physical cache content.</p><div class="resource-table"><div><strong>mlx-lm@0.31.3</strong><em class="ok">READY</em><button>Inspect</button></div><div><strong>mlx-vlm@0.6.3</strong><em class="ok">READY</em><button>Inspect</button></div><div><strong>optiq@0.2.15</strong><em>REPAIR</em><button data-scene="a-doctor">Fix</button></div><div><strong>qwen-optiq</strong><code>70a3aa32c7fe…</code><button>Inspect model</button></div></div><div class="actions"><button>⌕ Search catalog</button><button>＋ Install runtime</button><button>＋ Install model</button><button>◫ Scan cache</button><button>Review eviction</button></div></section>
          <section class="scene" id="a-system"><button class="back" data-scene="a-home">← Overview</button><h1>This machine</h1><div class="card-grid"><article class="card"><span class="ok">● RUNNING</span><h2>Supervisor</h2><p>Read-only commands never start it.</p><button>Restart…</button> <button class="danger">Stop…</button></article><article class="card"><span class="ok">● READY</span><h2>Gateway</h2><code>127.0.0.1:8766/v1</code><p>Routes by service name.</p><button>Copy endpoint</button></article><article class="card wide"><h2>Configuration</h2><p>Validated TOML with atomic backup and equivalent CLI/TUI edits.</p><button>Edit in mlxctl</button> <button>View TOML</button> <button>Validate</button></article></div></section>
          <section class="scene" id="a-ops"><button class="back" data-scene="a-home">← Overview</button><h1>Logs, metrics, progress, and runs</h1><div class="log">supervisor&nbsp; validating service coding<br>runtime&nbsp;&nbsp;&nbsp;&nbsp; optiq@0.2.15 capabilities loaded<br><span>error&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; option --max-context is unsupported</span></div><div class="empty">No active durable jobs and no request metrics.</div></section>
        </section>
        <aside class="inspector"><p class="eyebrow">SELECTED SERVICE</p><h2>coding</h2><em>START BLOCKED</em><dl><dt>Model</dt><dd>qwen-optiq</dd><dt>Runtime</dt><dd>optiq@0.2.15</dd><dt>Route</dt><dd>coding</dd><dt>Latest run</dt><dd>Rejected</dd></dl><button class="primary" disabled>▶ Start</button><button data-scene="a-service">Open details</button><button data-scene="a-doctor">Repair blocker</button></aside>
      </div>
    </main>`;
}

function launcherVariant() {
  return `
    <main class="variant launcher">
      <header class="launcher-head"><div class="brand">◈ <strong>mlxctl</strong><small> What would you like to do?</small></div>${fixture}</header>
      <div class="command-box">&gt;_ <input name="intent-query" aria-label="Intent search" placeholder="Run a model, find logs, inspect a runtime, change configuration…"><kbd>⌘ K</kbd></div>
      <nav class="intent-nav"><button class="selected" data-scene="b-home">Today</button><button data-scene="b-first">Run a model</button><button data-scene="b-library">Library</button><button data-scene="b-ops">Operations</button><button data-scene="b-system">System</button></nav>
      <section class="launcher-canvas">
        <section class="scene active" id="b-home"><p class="eyebrow">LOCAL INFERENCE</p><h1>One clear next action.</h1><p>Repair OptiQ before starting coding.</p><article class="focus"><b>01</b><div><em>BLOCKING</em><h2>Make coding runnable</h2><p><code>--max-context</code> is not advertised by optiq@0.2.15. No process started.</p><button class="primary" data-scene="b-doctor">Review safe repair</button> <button data-scene="b-service">Inspect service</button></div><small>Touches<br><strong>1 runtime</strong><br><strong>1 service</strong><br><strong>0 model files</strong></small></article><div class="intent-grid"><button data-scene="b-first"><b>＋</b><span><strong>Create a service</strong><small>Model → runtime → route</small></span></button><button data-scene="b-library"><b>⌕</b><span><strong>Find or install a model</strong><small>Catalog, cache, fit, trust</small></span></button><button data-scene="b-ops"><b>⌁</b><span><strong>Understand what happened</strong><small>Runs, jobs, logs, metrics</small></span></button><button data-scene="b-system"><b>⚙</b><span><strong>Manage this machine</strong><small>Supervisor, Gateway, config, Doctor</small></span></button></div><button class="recent" data-scene="b-service"><i class="stopped"></i><strong>coding</strong><span>qwen-optiq</span><span>optiq@0.2.15</span><em>BLOCKED</em><b>›</b></button></section>
        ${firstRun("b")}${serviceDetail("b")}${doctor("b")}
        <section class="scene" id="b-library"><button class="back" data-scene="b-home">← Today</button><h1>Find, install, and understand models</h1><p>Catalog Candidates, Model Installations, and Cached Revisions remain distinct.</p><div class="tile-grid"><button>⌕<strong>Catalog</strong><small>Remote and local candidates</small></button><button>▱<strong>Installed</strong><small>1 exact managed revision</small></button><button>◫<strong>Cache</strong><small>Official shared-cache operations</small></button><button>◎<strong>Assessments</strong><small>Fit, compatibility, trust</small></button><button>⬡<strong>Runtimes</strong><small>Install, update, inspect, remove</small></button></div></section>
        <section class="scene" id="b-ops"><button class="back" data-scene="b-home">← Today</button><h1>Runs, durable work, logs, and metrics</h1><div class="event bad">× coding launch rejected · no process created</div><div class="event ok">✓ Gateway ready</div><div class="empty">No active jobs and no request metrics.</div></section>
        <section class="scene" id="b-system"><button class="back" data-scene="b-home">← Today</button><h1>This machine</h1><div class="tile-grid"><button><span class="ok">● Running</span><strong>Supervisor</strong><small>Restart or stop explicitly</small></button><button><span class="ok">● Ready</span><strong>Gateway</strong><small>127.0.0.1:8766/v1</small></button><button>⬡<strong>Runtime Installations</strong><small>3 exact versions</small></button><button>⚙<strong>Configuration</strong><small>Edit and validate TOML here</small></button><button class="attention" data-scene="b-doctor">✚<strong>Doctor</strong><small>1 repair required</small></button></div></section>
      </section>
      <footer class="keybar">↑↓ Move&nbsp;&nbsp; ↵ Open&nbsp;&nbsp; ⌘K Command&nbsp;&nbsp; ? Help <span>Static fixture</span></footer>
    </main>`;
}

function graphVariant() {
  return `
    <main class="variant graph">
      <header class="graph-head"><div class="brand">◈ <strong>mlxctl / graph</strong><small> desired ↔ observed</small></div><nav><button class="selected" data-scene="c-home">Graph</button><button data-scene="c-first">New service</button><button data-scene="c-service">Inspect coding</button><button data-scene="c-ops">Operations</button></nav>${fixture}</header>
      <div class="graph-tools">ALL RESOURCES · NEEDS ATTENTION 1 <span>Legend: <b class="blue">— desired</b> <b class="ok">— observed</b> <b class="yellow">— blocked</b></span></div>
      <section class="scene active graph-scene" id="c-home"><div class="lanes"><article><header>01 <strong>SUPPLY</strong><small>Runtimes · models · cache</small></header><button class="node problem"><small>RUNTIME INSTALLATION</small><strong>optiq@0.2.15</strong><span>Installed · probed</span><em>! REPAIR</em></button><button class="node"><small>MODEL INSTALLATION</small><strong>qwen-optiq</strong><span>Qwen3.6-35B-A3B-OptiQ-4bit</span><code>70a3aa32c7fe…</code><em class="ok">✓ CACHED</em></button><footer>⌕ Catalog · ▱ Installed · ◫ Cache · ⬡ Runtimes</footer></article><i class="edge">model + runtime →</i><article><header>02 <strong>INFERENCE</strong><small>Services · runs</small></header><button class="node problem" data-scene="c-service"><small>INFERENCE SERVICE</small><strong>coding</strong><span>Gateway route: coding</span><em>■ STOPPED · BLOCKED</em></button><button class="node bad-node" data-scene="c-service"><small>LATEST SERVICE RUN</small><strong>run_coding_latest</strong><span>Rejected before process creation</span><em>× VALIDATION FAILED</em></button><footer><button data-scene="c-first">＋ New service</button> · <button data-scene="c-ops">↻ Runs</button></footer></article><i class="edge blocked">route unavailable ⇢</i><article><header>03 <strong>CLIENT EDGE</strong><small>Gateway · routes</small></header><button class="node"><small>GATEWAY</small><strong>ready</strong><code>127.0.0.1:8766/v1</code><em class="ok">● LOOPBACK</em></button><button class="node problem"><small>GATEWAY ROUTE</small><strong>model: coding</strong><span>Unavailable while stopped</span><em>! 503</em></button><footer>⇄ Gateway · ▣ Copy client config</footer></article></div><aside class="graph-inspector"><p class="eyebrow">SELECTED NODE</p><h2>coding</h2><em>START BLOCKED</em><p>Runtime cannot express one configured option.</p><button class="primary" disabled>▶ Start</button><button data-scene="c-service">Inspect subgraph</button><button data-scene="c-doctor">Doctor: repair runtime</button><dl><dt>Model</dt><dd>qwen-optiq</dd><dt>Runtime</dt><dd>optiq@0.2.15</dd><dt>Route</dt><dd>coding</dd><dt>Metrics</dt><dd>No samples</dd></dl></aside></section>
      <section class="scene graph-workbench" id="c-first"><button class="back" data-scene="c-home">← Graph</button><p class="eyebrow">SERVICE BUILDER</p><h1>Connect the resources</h1><div class="builder"><article><p>1 · RUNTIME</p><button class="node problem"><strong>optiq@0.2.15</strong><em>REPAIR</em></button><button>mlx-lm@0.31.3 · ready</button><button>mlx-vlm@0.6.3 · ready</button><button>＋ Install runtime</button></article><article><p>2 · MODEL</p><button class="node"><strong>qwen-optiq</strong><code>70a3aa32c7fe…</code><em class="ok">CACHED</em></button><button>⌕ Browse mlx-community</button><button>⌕ Search all candidates</button><button>◫ Inspect cache</button></article><article><p>3 · NAME &amp; ROUTE</p><div class="composition">[optiq] + [qwen] → [coding]</div><label>Service <input name="service-name" value="coding"></label><label>Route <input name="gateway-route" value="coding"></label><article class="inline-alert"><strong>Connection blocked</strong><p>Runtime capability conflict.</p><button data-scene="c-doctor">Repair</button></article><button class="primary" disabled>Review and create →</button></article></div><div class="progress">✓ Supervisor ─ ✓ Runtime ─ <b>● Model</b> ─ ○ Service ─ ○ Review</div></section>
      <section class="scene graph-workbench" id="c-service"><button class="back" data-scene="c-home">← Graph</button><p class="eyebrow">RESOURCE SUBGRAPH</p><h1>coding</h1><div class="subgraph"><span>[ Model<br><b>qwen-optiq</b> ]</span><i>＋</i><span class="problem">[ Runtime<br><b>optiq@0.2.15</b> ]</span><i>→</i><span>[ Service<br><b>coding</b> ]</span><i class="bad">×</i><span>[ Route<br><b>model: coding</b> ]</span></div><div class="tabs"><button class="selected">State diff</button><button>Runs</button><button>Logs</button><button>Metrics</button><button>Configuration</button></div><div class="state-diff"><article><p>DESIRED</p><div class="log">model = qwen-optiq<br>runtime = optiq@0.2.15<br>route = coding<br>activation = manual</div></article><article><p>OBSERVED</p><div class="log">state = stopped<br>process = null<br>upstream = null<br>metrics = no samples</div></article><article><p>EXPLANATION</p><h2>Launch option rejected</h2><p><code>--max-context</code> is not advertised.</p><button data-scene="c-doctor">Open repair graph →</button></article></div></section>
      ${doctor("c")}
      <section class="scene graph-workbench" id="c-ops"><button class="back" data-scene="c-home">← Graph</button><h1>Operations connected to resources</h1><div class="event bad">× coding launch rejected · links: coding, optiq@0.2.15, run_coding_latest</div><div class="event ok">✓ Gateway ready · links: Supervisor, Gateway</div><div class="empty">No active jobs and no request metrics.</div></section>
      <footer class="graph-footer"><button>◎ Supervisor</button><button>⇄ Gateway</button><button>⬡ Runtimes</button><button>▱ Models</button><button>◆ Services</button><button>↻ Runs</button><button>⌁ Logs &amp; metrics</button><button>⚙ Config</button><button class="attention" data-scene="c-doctor">✚ Doctor · 1</button></footer>
    </main>`;
}

const variants = { a: consoleVariant, b: launcherVariant, c: graphVariant };
const order = Object.keys(variants);
const prototype = document.querySelector("#prototype");
const palette = document.querySelector(".palette");
let active = new URLSearchParams(location.search).get("variant") || "a";
if (!variants[active]) active = "a";

function bind() {
  document.querySelectorAll("[data-scene]").forEach((button) => button.addEventListener("click", () => {
    document.querySelectorAll(".scene").forEach((scene) => scene.classList.remove("active"));
    document.querySelector(`#${button.dataset.scene}`)?.classList.add("active");
    button.closest("nav")?.querySelectorAll("button").forEach((item) => item.classList.toggle("selected", item === button));
  }));
  document.querySelectorAll(".palette-trigger").forEach((button) => button.addEventListener("click", () => { palette.hidden = false; palette.querySelector("input").focus(); }));
}

function render(key, replace = false) {
  active = key;
  prototype.innerHTML = variants[key]();
  document.querySelector("#variant-label").textContent = labels[key];
  const url = new URL(location.href);
  url.searchParams.set("variant", key);
  history[replace ? "replaceState" : "pushState"]({}, "", url);
  bind();
}

function cycle(delta) {
  render(order[(order.indexOf(active) + delta + order.length) % order.length]);
}

document.querySelector("#previous").addEventListener("click", () => cycle(-1));
document.querySelector("#next").addEventListener("click", () => cycle(1));
palette.addEventListener("click", (event) => { if (event.target === palette) palette.hidden = true; });
window.addEventListener("popstate", () => render(new URLSearchParams(location.search).get("variant") || "a", true));
window.addEventListener("keydown", (event) => {
  const editing = ["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName) || event.target.isContentEditable;
  if (!editing && event.key === "ArrowLeft") cycle(-1);
  if (!editing && event.key === "ArrowRight") cycle(1);
  if (event.key === "Escape") palette.hidden = true;
  if (!editing && (event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); palette.hidden = false; palette.querySelector("input").focus(); }
});

render(active, true);
