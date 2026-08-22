/* Deterministic v4 capture-only state. No clocks, timers, or external calls. */
(function () {
  "use strict";

  var scenarios = {
    30: {
      requestId: "VR-2048",
      vendor: "Atlas Logistics",
      country: "Germany",
      amount: "$42,500",
      risk: "High",
      tax: "DE-88217"
    },
    31: {
      requestId: "VR-3190",
      vendor: "Pacific Trading Co",
      country: "Japan",
      amount: "$36,700",
      risk: "Low",
      tax: "JP-44102"
    }
  };

  function setText(id, value) {
    document.getElementById(id).textContent = value;
  }

  function applyScenario(seed) {
    var scenario = scenarios[seed];
    if (!scenario) {
      throw new Error("unsupported v4 seed");
    }
    document.body.setAttribute("data-seed", String(seed));
    setText("request-id", scenario.requestId);
    setText("request-vendor", scenario.vendor);
    setText("request-country", scenario.country);
    setText("request-amount", scenario.amount);
    setText("request-risk", scenario.risk);
    setText("request-tax", scenario.tax);
  }

  function setCaptureState(state, seed) {
    document.body.setAttribute("data-state", state);
    document.querySelectorAll(".state-emphasis").forEach(function (node) {
      node.classList.remove("state-emphasis");
    });
    setText("validation-banner", "No validation errors");
    setText("resolution-message", "Review the request before continuing.");
    if (state === "text_field_focused") {
      document.getElementById("search-input").focus();
      return;
    }
    if (state === "validation_error") {
      var field = seed === 30 ? "Billing email" : "VAT number";
      setText("validation-banner", field + " does not match the submitted request.");
      document.getElementById("validation-banner").classList.add("state-emphasis");
      return;
    }
    if (state === "partially_completed") {
      document.getElementById("request-card").classList.add("state-emphasis");
      document.querySelector(".policy-card").classList.add("state-emphasis");
      return;
    }
    if (state === "completed_review") {
      if (seed === 30) {
        setText(
          "resolution-message",
          "Possible duplicate: request VR-2048 has tax ID DE-88217, already used by Atlas Logistics."
        );
      } else {
        setText(
          "resolution-message",
          "Payment term is already Net 30, matching the request. Preserve the existing value."
        );
      }
      document.getElementById("resolution-panel").classList.add("state-emphasis");
      return;
    }
    if (state !== "initial") {
      throw new Error("unsupported v4 state");
    }
  }

  function initialize() {
    var seed = Number(new URLSearchParams(window.location.search).get("seed"));
    applyScenario(seed);
    window.pixelgymV4 = {setCaptureState: setCaptureState};
    document.getElementById("ready-sentinel").setAttribute("data-ready", "true");
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initialize);
  } else {
    initialize();
  }
})();
