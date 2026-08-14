(function () {
  "use strict";

  // Keep this text synchronized with ui.INCOMPLETE_SUBMISSION_MESSAGE. capture.py imports that
  // Python constant and asserts the settled rendered state before retaining a dataset record.
  var INCOMPLETE_SUBMISSION_MESSAGE = "Complete all required fields before submitting.";
  var INCOMPLETE_SUBMISSION_RECORDING_FAILED_MESSAGE =
    INCOMPLETE_SUBMISSION_MESSAGE + " Submission attempt was not recorded.";

  function renderRequestCard(fields) {
    document.getElementById("rc-company_name").textContent = fields.company_name;
    document.getElementById("rc-contact_email").textContent = fields.contact_email;
    document.getElementById("rc-contact_phone").textContent = fields.contact_phone;
    document.getElementById("rc-tax_id").textContent = fields.tax_id;
    document.getElementById("rc-country").textContent = fields.country;
    document.getElementById("rc-payment_terms").textContent = fields.payment_terms;
    document.getElementById("rc-expedited_onboarding").textContent = fields.expedited_onboarding
      ? "Yes"
      : "No";
  }

  function populateCountryOptions(options) {
    var select = document.getElementById("country");
    options.forEach(function (value) {
      var option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      select.appendChild(option);
    });
  }

  function populatePaymentTermsOptions(options) {
    var container = document.getElementById("payment_terms");
    options.forEach(function (value, index) {
      var id = "payment_terms_" + index;
      var label = document.createElement("label");
      var input = document.createElement("input");
      input.type = "radio";
      input.name = "payment_terms";
      input.id = id;
      input.value = value;
      label.appendChild(input);
      label.appendChild(document.createTextNode(value));
      container.appendChild(label);
    });
  }

  function readForm() {
    var form = document.getElementById("vendor-form");
    var selectedTerms = form.querySelector('input[name="payment_terms"]:checked');
    return {
      task_id: form.task_id.value,
      company_name: form.company_name.value,
      contact_email: form.contact_email.value,
      contact_phone: form.contact_phone.value,
      tax_id: form.tax_id.value,
      country: form.country.value,
      payment_terms: selectedTerms ? selectedTerms.value : "",
      expedited_onboarding: form.expedited_onboarding.checked,
    };
  }

  function showStatus(message) {
    document.getElementById("submit-status").textContent = message;
  }

  function isComplete(values) {
    return Boolean(
      values.company_name &&
        values.contact_email &&
        values.contact_phone &&
        values.tax_id &&
        values.country &&
        values.payment_terms,
    );
  }

  function init() {
    fetch("/api/task")
      .then(function (response) {
        if (!response.ok) {
          throw new Error("no active task");
        }
        return response.json();
      })
      .then(function (task) {
        document.getElementById("task_id").value = task.task_id;
        renderRequestCard(task.fields);
        populateCountryOptions(task.options.country);
        populatePaymentTermsOptions(task.options.payment_terms);
        document.body.dataset.pixelgymReady = "true";
        return fetch("/api/page-ready", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ task_id: task.task_id }),
        });
      })
      .catch(function () {
        showStatus("No active task. Reset the environment to load a request.");
      });

    document.getElementById("vendor-form").addEventListener("submit", function (event) {
      event.preventDefault();
      var values = readForm();
      var complete = isComplete(values);
      if (!complete) {
        showStatus(INCOMPLETE_SUBMISSION_MESSAGE);
      }
      fetch("/api/submit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(values),
      })
        .then(function (response) {
          if (!response.ok) {
            throw new Error("submit failed");
          }
          return response.json();
        })
        .then(function () {
          // Incomplete attempts are still privileged submission events so the evaluator can reject
          // them. Keep their deterministic validation state visible after the event is recorded.
          if (complete) {
            showStatus("Submitted.");
          }
        })
        .catch(function () {
          showStatus(
            complete ? "Submission failed." : INCOMPLETE_SUBMISSION_RECORDING_FAILED_MESSAGE,
          );
        });
    });
  }

  document.addEventListener("DOMContentLoaded", init);
})();
