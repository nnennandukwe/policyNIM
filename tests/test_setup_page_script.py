"""Browser setup script behavior, exercised in Node with a minimal DOM double."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "src" / "policynim" / "assets" / "setup" / "setup.js"
NODE = shutil.which("node")

HARNESS = r"""
"use strict";
const fs = require("fs");
const vm = require("vm");

const elements = new Map();
const buttons = [];
const listeners = new Map();
function element(id) {
  if (!elements.has(id)) {
    const node = {
      id, value: "", hidden: false, checked: false, disabled: false, textContent: "",
      dataset: {}, setAttribute() {}, removeAttribute() {}, replaceChildren() {}, append() {},
      addEventListener(type, handler) { listeners.set(`${id}:${type}`, handler); },
    };
    elements.set(id, node);
  }
  return elements.get(id);
}
const preview = element("preview-button");
preview.dataset.action = "preview";
preview.addEventListener = (type, handler) => { preview.click = handler; };
buttons.push(preview);

const bodies = [];
const context = {
  location: { hash: "#token=fixture-token", pathname: "/" },
  history: { replaceState() {} },
  URLSearchParams,
  document: {
    getElementById: element,
    createElement: () => element(`created-${Math.random()}`),
    querySelector: (selector) => selector === "input[name=deployment]:checked"
      ? { value: "local" } : null,
    querySelectorAll: (selector) => selector === "[data-action]" ? buttons : [],
  },
  fetch: async (url, options) => {
    if (url === "/api/preview") bodies.push(JSON.parse(options.body));
    return {
      ok: true,
      json: async () => ({
        generations_dir: "/tmp/generations",
        review_digest: "digest",
        catalog: { effective_count: 0, digest: "digest", policies: [] },
      }),
    };
  },
};
vm.createContext(context);
element("source-mode").value = "baseline";
element("vector-backend").value = "sqlite";
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
function choose(mode) {
  element("source-mode").value = mode;
  listeners.get("setup-form:change")({ target: element("source-mode") });
}

(async () => {
  const observed = {};
  element("extra-packs").value = "/packs/team\n /packs/platform \n";
  listeners.get("setup-form:change")({ target: element("extra-packs") });
  choose("directory");
  element("source-path").value = "/policies/custom";
  observed.hiddenField = element("extra-packs-field").hidden;
  observed.hiddenValue = element("extra-packs").value;
  preview.click();
  await settle();
  choose("baseline");
  observed.restoredField = element("extra-packs-field").hidden;
  observed.restoredValue = element("extra-packs").value;
  preview.click();
  await settle();
  observed.previews = bodies.map((body) => ({
    source_mode: body.source_mode, additional_packs: body.additional_packs,
  }));
  process.stdout.write(JSON.stringify(observed));
})();
"""


@pytest.mark.skipif(NODE is None, reason="Node.js is required to run the setup page script.")
def test_source_mode_switch_keeps_hidden_team_packs_out_of_previews(tmp_path: Path) -> None:
    """Pack paths survive a mode that hides them, but only visible fields reach previews."""
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS)
    completed = subprocess.run(
        [str(NODE), str(harness), str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    observed = json.loads(completed.stdout)

    assert observed["hiddenField"] is True
    assert observed["hiddenValue"] == "/packs/team\n /packs/platform \n"
    assert observed["restoredField"] is False
    assert observed["restoredValue"] == "/packs/team\n /packs/platform \n"
    assert observed["previews"] == [
        {"source_mode": "directory", "additional_packs": []},
        {"source_mode": "baseline", "additional_packs": ["/packs/team", "/packs/platform"]},
    ]
