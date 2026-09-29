/* BenchLink screen behaviour. Calls the JSON API with the session's CSRF token and
   reloads the page. BenchLink never sends messages and has no SportsEngine connection. */
(function () {
  "use strict";

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  }

  function showStatus(message) {
    var box = document.getElementById("status");
    if (box) {
      box.textContent = message;
    } else {
      window.alert(message);
    }
  }

  function callApi(method, path, payload) {
    var options = {
      method: method,
      credentials: "same-origin",
      headers: {"Content-Type": "application/json", "X-CSRF-Token": csrfToken()}
    };
    if (payload !== undefined) {
      options.body = JSON.stringify(payload);
    }
    return fetch(path, options).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok) {
          throw new Error(data.error || ("Request failed (" + response.status + ")"));
        }
        return data;
      });
    });
  }

  function fail(error) {
    showStatus(error.message);
  }

  function readFields(form) {
    var payload = {};
    var fields = form.querySelectorAll("input[name], select[name], textarea[name]");
    Array.prototype.forEach.call(fields, function (field) {
      if (field.type === "file") {
        return;
      }
      var value = field.value;
      var kind = field.getAttribute("data-type");
      if (field.type === "checkbox") {
        payload[field.name] = field.checked;
      } else if (kind === "int") {
        if (value !== "") {
          payload[field.name] = parseInt(value, 10);
        }
      } else if (value !== "" || field.hasAttribute("data-keep-empty")) {
        payload[field.name] = value;
      }
    });
    return payload;
  }

  function submitImport(form) {
    var payload = readFields(form);
    var mappingText = (payload.mapping || "").trim();
    delete payload.mapping;
    if (mappingText) {
      try {
        payload.mapping = JSON.parse(mappingText);
      } catch (e) {
        showStatus("The header mapping must be valid JSON, for example {\"Skater\": \"full_name\"}.");
        return;
      }
    }
    var fileInput = form.querySelector('input[type="file"]');
    var file = fileInput && fileInput.files && fileInput.files[0];
    var ready = file ? file.text().then(function (text) {
      payload.csv = text;
      payload.filename = file.name;
    }) : Promise.resolve();
    ready.then(function () {
      if (!payload.csv) {
        throw new Error("Choose a CSV file or paste its text first.");
      }
      return callApi("POST", form.getAttribute("data-path"), payload);
    }).then(function (data) {
      window.location.href = form.getAttribute("data-next") + data["import"].id;
    }).catch(fail);
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest("button[data-path]");
    if (!button) {
      return;
    }
    event.preventDefault();
    var raw = button.getAttribute("data-body");
    callApi(button.getAttribute("data-method") || "POST", button.getAttribute("data-path"),
            raw ? JSON.parse(raw) : undefined)
      .then(function () { window.location.reload(); })
      .catch(fail);
  });

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form.hasAttribute("data-path")) {
      return;
    }
    event.preventDefault();
    if (form.getAttribute("data-kind") === "import") {
      submitImport(form);
      return;
    }
    callApi(form.getAttribute("data-method") || "POST", form.getAttribute("data-path"), readFields(form))
      .then(function () { window.location.reload(); })
      .catch(fail);
  });
}());
