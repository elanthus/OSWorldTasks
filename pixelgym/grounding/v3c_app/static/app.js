/* v3c data-table variant — static data, minimal JS.
   No animation, no timers, no network calls. */

(function () {
  "use strict";

  function init() {
    // Row selection (for completed_review state)
    document.querySelectorAll("#vendor-table tbody tr").forEach(function (row) {
      row.addEventListener("click", function () {
        document.querySelectorAll("#vendor-table tbody tr").forEach(function (r) {
          r.classList.remove("selected");
        });
        this.classList.add("selected");
      });
    });

    // Delete confirmation (for validation_error state)
    document.querySelectorAll(".btn-delete").forEach(function (btn) {
      btn.addEventListener("click", function (e) {
        e.stopPropagation();
        var row = this.closest("tr");
        var name = row.querySelector(".vendor-name").textContent;
        document.getElementById("status-message").textContent =
          "Are you sure you want to delete " + name + "?";
      });
    });

    document.getElementById("ready-sentinel").setAttribute("data-ready", "true");
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
