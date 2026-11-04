# Migration validation

## Final outcome

- The companion now lives in the actual `bs-simulators` repository on `develop`,
  version 0.1.0, with its own Python 3.13.2 environment and PDM-generated lockfile.
- PDM installed the full dependency set independently; it does not borrow system
  site packages or the original project's environment. The core package is
  installed at version 1.0.7 from the verified Git commit recorded below.
- Final companion suite: **45 passed in 6.63 s**, no skips.
- Original checkout after extraction: **40 passed in 6.02 s**, no skips.
- Both wheel and source distributions were built by PDM. The browser resource
  is included and all public wheel requirements remain version-based.
- Initial final-environment testing encountered five setup errors because the
  existing Windows pytest temporary directory was inaccessible. Repeating with
  a fresh explicit `--basetemp` and cache directory resolved those environment
  errors without changing or relaxing tests.
- The source prototype was removed only after validating its destination.
  A byte-for-byte recovery archive was created before cleanup. `AGENTS.md` and
  `.vscode/` in the original repository were preserved.
- Both repositories remain on `develop`; no commits, pushes or merges were made.

The following sections retain the earlier validation steps and their provenance.

## Source-tree verification

- Date: 2026-10-30.
- Interpreter: Python 3.13.1 from the existing benchmark-systems `.venv`.
- Simulator imports: staging `src/bs_simulators`, supplied through `PYTHONPATH`.
- Model imports: existing editable benchmark-systems source checkout, runtime version 1.0.7. Its installed distribution metadata still reports 1.0.0; this run does **not** establish installation/dependency resolution against a packaged 1.0.7 release.
- Result after the HTTP error-response correction: **44 passed, 1 skipped in 6.56 s**.
- The one skip is the wheel-content check: no wheel had been built when this run executed.

The migrated 41 simulator acceptance tests preserve numerical, zero-order-hold, clock-boundary, controller-source, quality, timestamp, structural-protocol, standalone-process and HTTP checks. The three additional checks executed here verify version 0.1.0, resource loading through `importlib.resources`, and importing the runtime in a fresh process without loading communications or viewers. The HTTP integration test also retrieves the packaged HTML resource through the server route.

Initial execution exposed intermittent Windows connection resets when rejecting POST requests before consuming their bodies. The destination HTTP implementation now drains small declared request bodies before returning errors; the complete source test suite subsequently passed. Tests retain the requirement for an HTTP 403 response rather than accepting connection resets.

## Distribution verification completed

`tests/test_distribution.py` inspects the built wheel under `dist/`, or the explicit `BS_SIMULATORS_WHEEL` path, and checks the HTML resource, metadata version, external benchmark-systems requirement, console entry point and absence of a bundled benchmark_systems package. After both distributions were built, the source suite completed with **45 passed in 7.22 s**, without skips.

The supervisor built the wheels from their source distributions:

- `migration-bs-simulators/dist/bs_simulators-0.1.0-py3-none-any.whl`.
- `core-migration-source/dist/benchmark_systems-1.0.7-py3-none-any.whl`.

Both wheels were installed with `--no-deps` into the separate `wheel-validation-env`, created with `--system-site-packages`. With `PYTHONPATH` removed and the working directory set to the artifacts root, the combined installed-code suite completed with **85 passed in 14.26 s**: 45 simulator/distribution tests and 40 original offline/core tests.

Observed imports for `bs_simulators`, its runtime, `benchmark_systems` and the HTML resource all point inside `wheel-validation-env/Lib/site-packages`; runtime and distribution versions agree at 0.1.0 and 1.0.7 respectively. `bs-simulators.exe --help` also executed successfully. The subprocess HTTP/CLI tests and resource-serving checks ran against this installed code, without source `PYTHONPATH`.

This was **installed-code validation with borrowed dependencies**, not a fully independent installation from a package index. Scientific dependencies came from the pre-existing system/user site packages. Pytest was unavailable in the new environment, so the test runner appended only the original `.venv/Lib/site-packages` to the end of `sys.path` to import test tooling; it did not process that directory's `.pth` files. Installed wheel paths retained precedence, and spawned simulator processes used the separate environment's Python without that runner-only addition.

The original checkout and original tests were not edited by the migration tester.

## Final repository environment

Read-only checks after transfer to the sibling `bs-simulators` repository confirm:

- Active branch: `develop`.
- Own PDM-managed `.venv`: CPython 3.13.2, `include-system-site-packages=false`, user-site imports disabled.
- `bs_simulators` imports from the target repository's `src/bs_simulators` through its development installation; no staging source path is required.
- `benchmark_systems` imports from the target `.venv/Lib/site-packages` at version 1.0.7, not from the sibling editable checkout.
- Both `pdm.lock` and installed `direct_url.json` identify Git commit `0334ffd8179fa5355236d0aeb00aef42b3280517` from `https://github.com/ggonzm/benchmark-systems.git`.
- The Git source is a development resolution override. The target wheel still declares the public version requirement `benchmark-systems>=1.0.7,<2`; its METADATA contains no Git URL or staging paths.
- The target wheel includes `bs_simulators/viewers/pendulum.html` and excludes Python bytecode/cache files.
- Scanning target source, examples, tests, README, AGENTS and pyproject found no staging paths or `benchmark_systems.simulation` imports. README commands describe the own PDM environment, pinned installed core and optional replay dependencies.

The supervisor is running the target's own PDM test suite; the tester did not start a competing test run. Its result should be recorded separately before attributing the earlier borrowed-environment test results to this final environment.
