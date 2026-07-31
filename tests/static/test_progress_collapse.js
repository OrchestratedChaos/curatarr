// Tests for the run page's progress-line collapsing (web/static/app.js).
//
// Run with macOS's bundled JavaScriptCore:
//   /System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc \
//     tests/static/test_progress_collapse.js
//
// tests/test_web_static_assets.py drives this from pytest and skips when
// no JS engine is available, so CI on Linux stays green.
//
// The DOM stub below models only what the renderer touches: a parent
// whose children are text nodes and at most one trailing <span> for the
// line currently animating. textContent on the parent concatenates its
// children, and ASSIGNING it destroys them - which is the exact behavior
// the trim path depends on and the reason liveNode has to be dropped
// there.

// Engine compatibility: JavaScriptCore (macOS, bundled) provides
// print/readFile/quit as globals; Node provides console/fs/process
// instead. Shim whichever is missing so the same file runs under either -
// CI is Linux with Node, local dev is macOS with jsc, and a test that
// only ran in one place would be a test that silently stopped running.
if (typeof print === 'undefined') {
  var print = function (s) { console.log(s); };
}
if (typeof readFile === 'undefined') {
  var readFile = function (p) { return require('fs').readFileSync(p, 'utf8'); };
}
if (typeof quit === 'undefined') {
  var quit = function (c) { process.exit(c); };
}

var failures = 0;
var checks = 0;

function assertEqual(actual, expected, label) {
  checks++;
  if (actual !== expected) {
    failures++;
    print('FAIL: ' + label);
    print('  expected: ' + JSON.stringify(expected));
    print('  actual:   ' + JSON.stringify(actual));
  }
}

function makeNode(text) {
  return { nodeValue: text, children: null };
}

function makeElement() {
  var el = {
    childNodes: [],
    scrollTop: 0,
    clientHeight: 100,
    appendChild: function (n) { el.childNodes.push(n); return n; },
    removeChild: function (n) {
      var i = el.childNodes.indexOf(n);
      if (i >= 0) { el.childNodes.splice(i, 1); }
      return n;
    },
    insertBefore: function (n, ref) {
      var i = el.childNodes.indexOf(ref);
      if (i < 0) { el.childNodes.push(n); } else { el.childNodes.splice(i, 0, n); }
      return n;
    }
  };
  Object.defineProperty(el, 'textContent', {
    get: function () {
      return el.childNodes.map(function (n) { return n.nodeValue; }).join('');
    },
    set: function (v) {
      el.childNodes = v === '' ? [] : [makeNode(v)];
    }
  });
  Object.defineProperty(el, 'scrollHeight', {
    get: function () { return el.textContent.split('\n').length * 10; }
  });
  return el;
}

// Harness: load app.js's renderer by evaluating the file with stubs in
// place. Simpler and less brittle than duplicating the logic here, which
// would let the test pass while the shipped file was broken.
var frameQueue = [];
var output = makeElement();
var window = {
  requestAnimationFrame: function (fn) { frameQueue.push(fn); },
  CURATARR_JOB_RUNNING: false
};
var document = {
  createTextNode: function (t) { return makeNode(t); },
  createElement: function () {
    var span = makeNode('');
    Object.defineProperty(span, 'textContent', {
      get: function () { return span.nodeValue; },
      set: function (v) { span.nodeValue = v; }
    });
    return span;
  },
  getElementById: function () { return null; },
  querySelector: function () { return null; },
  querySelectorAll: function () { return []; },
  addEventListener: function () {}
};
function setTimeout(fn) { frameQueue.push(fn); }

function runFrames() {
  while (frameQueue.length) { frameQueue.shift()(); }
}

// --- the code under test, extracted from web/static/app.js -------------
// Pulled out by marker rather than re-implemented, so a change to the
// shipped renderer that breaks these rules fails here.
var src = readFile('web/static/app.js');
var start = src.indexOf('  var MAX_LINES = 5000;');
var end = src.indexOf('  function appendLine(text) {');
end = src.indexOf('\n  }', end) + 4;
if (start < 0 || end < 4) {
  print('FAIL: could not locate renderer block in web/static/app.js');
  quit(1);
}
eval(src.slice(start, end));
// ----------------------------------------------------------------------

function reset() {
  output.childNodes = [];
  pending.length = 0;
  lineCount = 0;
  flushScheduled = false;
  liveFamily = null;
  liveText = null;
  liveNode = null;
  frameQueue.length = 0;
}

// progressFamily ---------------------------------------------------------
assertEqual(progressFamily('Processing movie 321/337 (95%)'), 'Processing movie', 'family: plain');
assertEqual(progressFamily('\x1b[96mProcessing movie 5/9 (55%)\x1b[0m'), 'Processing movie', 'family: ansi-wrapped');
assertEqual(progressFamily('Processing 1/127 (0%)'), 'Processing', 'family: bare prefix');
assertEqual(progressFamily("Processing alice's watched 4/233 (1%)"), "Processing alice's watched", 'family: apostrophe');
assertEqual(progressFamily('Found 7 new movies to analyze'), null, 'family: not a progress line');
assertEqual(progressFamily('Adding 1 new high-scoring recommendations'), null, 'family: informational line');
assertEqual(progressFamily(''), null, 'family: empty');

// A same-family run collapses to a single line -----------------------------
reset();
for (var i = 1; i <= 337; i++) {
  appendLine('Processing movie ' + i + '/337 (' + Math.floor(i / 337 * 100) + '%)');
}
runFrames();
assertEqual(output.textContent, 'Processing movie 337/337 (100%)\n', 'collapse: 337 updates render as one line');

// The finished line is kept when different information follows ------------
appendLine('Movies cache updated');
runFrames();
assertEqual(
  output.textContent,
  'Processing movie 337/337 (100%)\nMovies cache updated\n',
  'commit: completed progress line survives the next real line'
);

// A different family starts a new line rather than overwriting ------------
reset();
appendLine('Processing movie 1/2 (50%)');
appendLine('Processing movie 2/2 (100%)');
appendLine("Processing alice's watched 1/2 (50%)");
appendLine("Processing alice's watched 2/2 (100%)");
appendLine('done');
runFrames();
assertEqual(
  output.textContent,
  "Processing movie 2/2 (100%)\nProcessing alice's watched 2/2 (100%)\ndone\n",
  'families: distinct prefixes each keep their own line'
);

// Interleaved informational lines are never collapsed ---------------------
reset();
appendLine('Connecting to Plex server...');
appendLine('Processing movie 1/3 (33%)');
appendLine('Processing movie 2/3 (66%)');
appendLine('Processing movie 3/3 (100%)');
appendLine('Analyzing library movies...');
runFrames();
assertEqual(
  output.textContent,
  'Connecting to Plex server...\nProcessing movie 3/3 (100%)\nAnalyzing library movies...\n',
  'ordering: informational lines keep their positions around a collapsed run'
);

// Committed lines land BEFORE the animating line, not after ---------------
reset();
appendLine('Processing movie 1/9 (11%)');
runFrames(); // live line now rendered as trailing node
appendLine('a real line');
appendLine('Processing movie 2/9 (22%)');
runFrames();
assertEqual(
  output.textContent,
  'Processing movie 1/9 (11%)\na real line\nProcessing movie 2/9 (22%)\n',
  'ordering: committed text is inserted before the live node'
);

// The live line survives a trim (which destroys all children) -------------
reset();
for (var j = 0; j < 5200; j++) { appendLine('line ' + j); runFrames(); }
appendLine('Processing movie 1/2 (50%)');
runFrames();
appendLine('Processing movie 2/2 (100%)');
runFrames();
var tail = output.textContent.split('\n');
assertEqual(tail[tail.length - 2], 'Processing movie 2/2 (100%)', 'trim: live line still renders after a trim');

// Progress updates must not grow the DOM without bound --------------------
reset();
for (var k = 1; k <= 1000; k++) {
  appendLine('Processing movie ' + k + '/1000 (' + Math.floor(k / 10) + '%)');
  runFrames();
}
assertEqual(output.textContent.split('\n').length - 1, 1, 'bounded: 1000 updates never exceed one rendered line');

print(failures === 0
  ? 'ok - ' + checks + ' checks passed'
  : 'FAILED - ' + failures + ' of ' + checks + ' checks failed');
quit(failures === 0 ? 0 : 1);
