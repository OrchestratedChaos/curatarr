// Run page behavior: toggle the user selector for engines that don't
// support a single-user run, and stream live output over SSE.
(function () {
  var engineSelect = document.getElementById('engine-select');
  var userSelect = document.getElementById('user-select');

  function syncUserSelect() {
    if (!engineSelect || !userSelect) return;
    var perUser = engineSelect.value === 'movie' || engineSelect.value === 'tv';
    userSelect.disabled = !perUser;
    if (!perUser) userSelect.value = 'all';
  }

  if (engineSelect) {
    engineSelect.addEventListener('change', syncUserSelect);
    syncUserSelect();
  }

  var output = document.getElementById('output');
  var stateEl = document.getElementById('job-state');

  function markDone(returncode) {
    if (stateEl) {
      stateEl.textContent = (String(returncode) === '0') ? 'succeeded' : 'failed';
    }
    var btn = document.querySelector('#run-form button[type="submit"]');
    if (btn) btn.disabled = false;
  }

  // #287: falls back to polling /run/status instead of a live stream
  // when the server's MAX_STREAM_SUBSCRIBERS_PER_JOB cap rejects a new
  // EventSource (see web/app.py's run_stream()) - still reflects the
  // run finishing, just without live-tailed output for this tab.
  function pollStatus() {
    var timer = setInterval(function () {
      fetch('/run/status')
        .then(function (resp) { return resp.json(); })
        .then(function (data) {
          if (data && data.state && data.state !== 'running') {
            clearInterval(timer);
            markDone(data.returncode);
          }
        })
        .catch(function () {
          // Transient fetch failure - just try again on the next tick.
        });
    }, 5000);
  }

  // #287: only ever open a stream for a job actually running right
  // now - a job record existing at all (even one that finished hours
  // ago) used to be enough to open one, which was needless: a
  // finished job's own subscribe() replays its backlog and closes
  // immediately (see web/app.py's run_stream()), so the only thing
  // that bug added was one more pointless connection.
  // Live-output rendering. `output.textContent += line` looks harmless
  // and is quadratic: READING textContent materializes the entire
  // accumulated log into a fresh string, and WRITING it destroys every
  // child node and rebuilds one - so each line costs O(total so far).
  // Setting scrollTop=scrollHeight on every line then forces a synchronous
  // layout of a steadily growing element on top of that.
  //
  // Measured in Chrome, appending N lines this way vs. the buffered
  // approach below: 500 -> 1089ms/5ms, 1000 -> 4333ms/10ms,
  // 2000 -> 16333ms/21ms, 4000 -> 66216ms/50ms. Time quadruples each
  // time N doubles. A real full run emitted 11505 lines, which
  // extrapolates to ~9 MINUTES of blocked main thread - the run page
  // "going unresponsive" partway through a long run.
  //
  // (This is a SECOND, independent quadratic from the server-side
  // backlog replay fixed in 2.10.85 - that one was in Job.try_subscribe,
  // this one is in the browser. Fixing the server made the page load
  // fast while the tab still locked up.)
  //
  // So: buffer incoming lines and write them once per animation frame,
  // appending a NEW text node instead of rewriting the whole thing.
  var MAX_LINES = 5000; // keep a bounded tail - a long run can emit far more
  var TRIM_TO = 4000;
  var pending = [];
  var flushScheduled = false;
  var lineCount = 0;

  // Counter/percentage progress updates overwrite in place rather than
  // stacking up. The recommender writes these with a bare \r and no
  // newline - correct in a terminal, where each overwrites the last -
  // but the subprocess pipe is opened in text mode, and universal-newline
  // translation turns every \r into its own line before it reaches us.
  // A 337-item scan arrived as 337 separate lines.
  //
  // Only a counter advancing under an unchanged prefix collapses. A line
  // carrying genuinely new information never does, and the final update
  // of a run (the 100%) is committed permanently once a different line
  // follows it, so completed steps stay in the log.
  //
  // Deliberately mirrored from web/job_runner.py's progress_family():
  // the server needs the rule to keep its stored log and backlog replay
  // clean, the client needs it because subscribers still receive every
  // individual update in order to animate the counter.
  var PROGRESS_RE = /^(.*?)\s*\d+\s*\/\s*\d+\s*\(\s*\d+\s*%\s*\)$/;
  var ANSI_RE = /\x1b\[[0-9;]*m/g;
  var liveFamily = null; // family of the progress run currently animating
  var liveText = null; // its latest text, not yet committed to the log
  var liveNode = null; // the trailing node it renders into

  function progressFamily(text) {
    var m = PROGRESS_RE.exec(String(text).replace(ANSI_RE, '').trim());
    return m ? m[1].trim() : null;
  }

  function nearBottom() {
    // Checked BEFORE appending, since appending changes scrollHeight.
    return output.scrollHeight - output.scrollTop - output.clientHeight < 40;
  }

  function renderLive() {
    if (liveText === null) { return; }
    if (!liveNode) {
      liveNode = document.createElement('span');
      output.appendChild(liveNode);
    }
    liveNode.textContent = liveText + '\n';
  }

  // Fold the finished progress line into the permanent log. Queued as a
  // normal pending line so it takes the same trim/scroll path as any
  // other, rather than becoming a second way for text to reach the DOM.
  function commitLive() {
    if (liveText === null) { return; }
    pending.push(liveText);
    liveText = null;
    liveFamily = null;
    if (liveNode) {
      output.removeChild(liveNode);
      liveNode = null;
    }
  }

  function scheduleFlush() {
    if (flushScheduled) { return; }
    flushScheduled = true;
    if (window.requestAnimationFrame) {
      window.requestAnimationFrame(flushLines);
    } else {
      setTimeout(flushLines, 16);
    }
  }

  function flushLines() {
    flushScheduled = false;
    if (!pending.length && liveText === null) { return; }
    // Don't yank the view back down if the user has scrolled up to read
    // something - only keep following the tail if they were already there.
    var stick = nearBottom();
    if (pending.length) {
      var node = document.createTextNode(pending.join('\n') + '\n');
      // Committed lines must land BEFORE the animating progress line,
      // which is always the last child while its run is in flight.
      if (liveNode) {
        output.insertBefore(node, liveNode);
      } else {
        output.appendChild(node);
      }
      lineCount += pending.length;
      pending.length = 0;
      if (lineCount > MAX_LINES) {
        // O(n), but only once every (MAX_LINES - TRIM_TO) lines, so the
        // amortized cost per line stays constant.
        var kept = output.textContent.split('\n').slice(-TRIM_TO);
        output.textContent = kept.join('\n');
        lineCount = kept.length;
        // Rewriting textContent destroyed every child, liveNode included.
        liveNode = null;
      }
    }
    renderLive();
    if (stick) { output.scrollTop = output.scrollHeight; }
  }

  function appendLine(text) {
    var family = progressFamily(text);
    if (family !== null) {
      // A different progress run starting means the previous one ended -
      // keep its last state rather than overwriting it with the new run.
      if (family !== liveFamily) { commitLive(); }
      liveFamily = family;
      liveText = text;
      scheduleFlush();
      return;
    }
    commitLive();
    pending.push(text);
    // requestAnimationFrame doesn't fire in a background tab, so a
    // hidden page would otherwise buffer without bound - flush directly
    // once enough has piled up.
    if (pending.length >= 1000) { flushLines(); return; }
    scheduleFlush();
  }

  if (output && window.CURATARR_JOB_RUNNING) {
    var source = new EventSource('/run/stream');

    source.onmessage = function (event) {
      appendLine(event.data);
    };

    source.addEventListener('done', function (event) {
      flushLines(); // don't leave the last few lines sitting in the buffer
      markDone(event.data);
      source.close();
    });

    source.addEventListener('busy', function (event) {
      appendLine(event.data);
      flushLines(); // terminal message - show it now, don't wait for a frame
      source.close();
      pollStatus();
    });

    // Deliberately no onerror handler that closes the source: a
    // dropped or timed-out connection (see web/app.py's
    // MAX_STREAM_SECONDS - a run can take many minutes, and a single
    // stream is never allowed to hold its server-side thread for the
    // whole thing) is not fatal - EventSource retries automatically on
    // its own unless the server responds with a non-2xx status (e.g.
    // the job record is gone entirely), which stops retrying for that
    // case on its own per spec.
  }
})();
