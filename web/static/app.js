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
  if (output && window.CURATARR_JOB_RUNNING) {
    var source = new EventSource('/run/stream');

    source.onmessage = function (event) {
      output.textContent += event.data + '\n';
      output.scrollTop = output.scrollHeight;
    };

    source.addEventListener('done', function (event) {
      markDone(event.data);
      source.close();
    });

    source.addEventListener('busy', function (event) {
      output.textContent += event.data + '\n';
      output.scrollTop = output.scrollHeight;
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
