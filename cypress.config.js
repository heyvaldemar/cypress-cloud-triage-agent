// The Cypress project: one spec against the orders page in app/, served by
// Cypress's own file server, so no web server is needed.
//
// After the run, the results go to out/cypress-results.json. That file is
// what this repository has instead of Cypress Cloud on kind: the triage
// stack's Cloud MCP stand-in serves the failures from it, and the CI trigger
// builds the run.completed payload from it (scripts/results-to-run.py).
// With a recorded run (`cypress run --record`), Cypress Cloud holds the same
// failures and the real Cloud MCP serves them.
const fs = require("fs");
const path = require("path");
const { defineConfig } = require("cypress");

module.exports = defineConfig({
  e2e: {
    specPattern: "cypress/e2e/**/*.cy.js",
    supportFile: false,
    video: false,
    screenshotOnRunFailure: false,
    retries: 0,
    setupNodeEvents(on) {
      on("after:run", (results) => {
        const out = path.join(__dirname, "out");
        fs.mkdirSync(out, { recursive: true });
        fs.writeFileSync(path.join(out, "cypress-results.json"), JSON.stringify(results, null, 2));
      });
    },
  },
});
