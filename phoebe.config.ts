// Phoebe tenant config for mikeallisonJS/cadent.
//
// Phoebe (https://github.com/JesusFilm/phoebe) picks up issues labelled
// `ready-for-agent`, works each one on a `phoebe/` branch in its own clone, runs
// the gates below, and opens a PR. This repo is one tenant of a Phoebe
// workspace: the workspace root discovers this file and runs the shared engine
// in its container. This file names only what is specific to this repo.
//
// No import on purpose: the container loads this file from a mount with no
// node_modules, and this repo has no `phoebe-agent` dependency. The fields are
// `PhoebeUserConfig` from phoebe-agent (docs/configuration.md).
//
// Everything else Phoebe reads lives in `.phoebe/` (`configDir` below): the
// engine's shipped prompts, unedited, in .phoebe/prompts/, and the gitignored
// secrets file .phoebe/.env (see .phoebe/.env.example):
// GH_TOKEN, a fine-grained PAT for this repo, and ANTHROPIC_AUTH_TOKEN, the
// provider key. The workspace image sends Claude Code through a shared proxy
// when that key is the one it is given.
//
// Gates from README.md (ruff) and test.yml (pytest; offscreen Qt, as on
// the macOS leg).

const config = {
  repoSlug: "mikeallisonJS/cadent",
  repoUrl: "https://github.com/mikeallisonJS/cadent.git",
  configDir: ".phoebe",
  installCommand: "uv sync --locked --extra dev",
  checkCommand: "uv run ruff check .",
  testCommand: "QT_QPA_PLATFORM=offscreen uv run pytest",
  readyCommand:
    "uv run ruff check . && QT_QPA_PLATFORM=offscreen uv run pytest",
  defaultProvider: "claude",
  providerEnv: { claude: "ANTHROPIC_AUTH_TOKEN" },

  // Phoebe's dependency bot (engine v0.17.0+, docs/configuration.md, "The
  // dependency kinds"), on a pipeline of its own beside the default `work`
  // one. `dependency-updates` files a daily issue per bundle of outdated
  // packages, `dependency-security` files severe advisories, and
  // `dependencies` works those issues into PRs. Their tickets wear
  // `phoebe:dependencies`, never `ready-for-agent`, so `work` never takes
  // one. Security scans every 6h instead of hourly: each scan includes an
  // agent run, and the workspace runs it for every tenant.
  pipelines: {
    dependencies: {
      pollIntervalMs: 600_000,
      kinds: {
        "dependency-updates": { path: "phoebe-agent/kinds/dependency-updates" },
        "dependency-security": {
          path: "phoebe-agent/kinds/dependency-security",
          every: "6h",
        },
        dependencies: { path: "phoebe-agent/kinds/dependencies-work" },
      },
    },
  },
};

export default config;
