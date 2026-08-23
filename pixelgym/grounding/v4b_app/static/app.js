(() => {
  "use strict";
  const params = new URLSearchParams(window.location.search);
  const stageIndex = Number(params.get("stage") || "0");
  const recovery = params.get("recovery") === "1";
  const episode = JSON.parse(document.getElementById("episode-data").textContent);

  function renderProgress(stage) {
    const labels = ["1 · Select", "2 · Decide", "3 · Confirm"];
    document.getElementById("progress").replaceChildren(...labels.map((label, index) => {
      const node = document.createElement("div");
      node.className = "progress-step" + (index < stage ? " done" : index === stage ? " current" : "");
      node.textContent = (index < stage ? "✓ " : "") + label;
      return node;
    }));
  }

  async function render() {
    document.getElementById("task-ref").textContent = episode.title;
    renderProgress(Math.min(stageIndex, 2));
    const banner = document.getElementById("recovery-banner");
    if (recovery) {
      banner.hidden = false;
      banner.textContent = "That selection did not satisfy the visible rule. Re-read this step and choose again.";
      document.getElementById("activity-text").textContent = "The prior selection was rejected; no workflow progress was awarded.";
    }
    if (stageIndex >= 3) {
      document.getElementById("family").textContent = "Workflow complete";
      document.getElementById("heading").textContent = "Request submitted successfully";
      document.getElementById("instruction").textContent = "All three review decisions were recorded.";
      document.getElementById("facts").innerHTML = '<div class="fact">The host will verify the final submission.</div>';
      document.getElementById("options").replaceChildren();
    } else {
      const stage = episode.stages[stageIndex];
      document.getElementById("family").textContent = episode.family.replaceAll("_", " → ");
      document.getElementById("heading").textContent = stage.heading;
      document.getElementById("instruction").textContent = stage.instruction;
      document.getElementById("facts").replaceChildren(...stage.facts.map((fact) => {
        const node = document.createElement("div"); node.className = "fact"; node.textContent = fact; return node;
      }));
      document.getElementById("options").replaceChildren(...stage.options.map((option) => {
        const button = document.createElement("button"); button.type = "button"; button.className = "option";
        button.id = `action-${option.semantic_id}`; button.dataset.semanticId = option.semantic_id;
        button.textContent = option.label; return button;
      }));
    }
    await document.fonts.ready;
    document.getElementById("ready-sentinel").dataset.ready = "true";
  }
  render();
})();
