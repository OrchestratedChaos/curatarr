// Connections screen behavior: "Test Connection" buttons POST the
// relevant fields (as currently typed, not yet saved) to
// /config/test/<service> and show the ok/fail message inline.
(function () {
  function fieldValue(name) {
    var el = document.querySelector('[name="' + name + '"]');
    return el ? el.value : '';
  }

  document.querySelectorAll('.test-connection').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var service = btn.dataset.service;
      var fieldMap = JSON.parse(btn.dataset.fieldMap || '{}');
      var payload = {};
      Object.keys(fieldMap).forEach(function (key) {
        payload[key] = fieldValue(fieldMap[key]);
      });

      var resultEl = document.querySelector('.test-result[data-result-for="' + service + '"]');
      if (resultEl) {
        resultEl.textContent = 'Testing...';
        resultEl.className = 'test-result';
      }
      btn.disabled = true;

      fetch('/config/test/' + service, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams(payload).toString(),
      })
        .then(function (resp) { return resp.json(); })
        .then(function (data) {
          if (resultEl) {
            resultEl.textContent = data.message;
            resultEl.className = 'test-result ' + (data.ok ? 'ok' : 'fail');
          }
        })
        .catch(function () {
          if (resultEl) {
            resultEl.textContent = 'Request failed';
            resultEl.className = 'test-result fail';
          }
        })
        .finally(function () {
          btn.disabled = false;
        });
    });
  });
})();
