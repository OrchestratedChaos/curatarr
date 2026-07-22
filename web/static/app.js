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
  if (output && window.CURATARR_HAS_JOB) {
    var stateEl = document.getElementById('job-state');
    var source = new EventSource('/run/stream');

    source.onmessage = function (event) {
      output.textContent += event.data + '\n';
      output.scrollTop = output.scrollHeight;
    };

    source.addEventListener('done', function (event) {
      if (stateEl) {
        stateEl.textContent = (event.data === '0') ? 'succeeded' : 'failed';
      }
      var btn = document.querySelector('#run-form button[type="submit"]');
      if (btn) btn.disabled = false;
      source.close();
    });

    source.onerror = function () {
      source.close();
    };
  }
})();
