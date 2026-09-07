/* v3b variant app — deterministic form initialization for capture.
   No animation, no timers, no network calls except the initial task fetch. */

(function () {
  "use strict";

  function init() {
    fetch("/api/task")
      .then(function (r) { return r.json(); })
      .then(function (task) {
        document.getElementById("task_id").value = task.task_id;

        // Populate country dropdown
        var countrySelect = document.getElementById("country");
        (task.options.country || []).forEach(function (c) {
          var opt = document.createElement("option");
          opt.value = c;
          opt.textContent = c;
          countrySelect.appendChild(opt);
        });

        // Populate region dropdown with deterministic values
        var regionSelect = document.getElementById("region");
        ["North", "South", "East", "West", "Central"].forEach(function (r) {
          var opt = document.createElement("option");
          opt.value = r;
          opt.textContent = r;
          regionSelect.appendChild(opt);
        });

        // Populate payment terms radio group
        var paymentDiv = document.getElementById("payment_terms");
        (task.options.payment_terms || []).forEach(function (term) {
          var label = document.createElement("label");
          var radio = document.createElement("input");
          radio.type = "radio";
          radio.name = "payment_terms";
          radio.value = term;
          label.appendChild(radio);
          label.appendChild(document.createTextNode(" " + term));
          paymentDiv.appendChild(label);
        });

        // Form submission handler
        document.getElementById("ready-sentinel").setAttribute("data-ready", "true");

        document.getElementById("vendor-form").addEventListener("submit", function (e) {
          e.preventDefault();
          var formData = {
            task_id: task.task_id,
            company_name: document.getElementById("company_name").value,
            contact_email: document.getElementById("contact_email").value,
            contact_phone: document.getElementById("contact_phone").value,
            tax_id: document.getElementById("tax_id").value,
            country: document.getElementById("country").value,
            payment_terms: (document.querySelector('input[name="payment_terms"]:checked') || {}).value || "",
            expedited_onboarding: document.getElementById("expedited_onboarding").checked,
          };
          fetch("/api/submit", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(formData),
          })
            .then(function (r) {
              return r.json().then(function (result) {
                return { ok: r.ok, result: result };
              });
            })
            .then(function (response) {
              var status = document.getElementById("submit-status");
              status.textContent = response.ok
                ? "Submission recorded."
                : response.result.detail;
            });
        });
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
