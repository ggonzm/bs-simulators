# Repository Guide

## Version Control
- Apply new changes on `develop`. Check the branch before editing and preserve existing local changes.
- Merge into `main` and push only when the user explicitly requests those actions.
- Before a user-requested merge into `main` for a new push, increment the patch version on `develop` and synchronize `pyproject.toml` and `src/bs_simulators/__init__.py`.
- This package starts at `0.1.0`; its version is independent of `benchmark-systems`.

## Environment
- Use Python 3.13 or later and PDM. Build with `pdm build`.
- Do not edit generated lockfiles or build/distribution files by hand.
- On this Windows/OneDrive checkout, build-directory cleanup may encounter read-only directories. Use the supported `pdm build -C no-clean-build=true` option when that occurs instead of editing generated files manually.
- Use `pdm lock -G dev -G replay` when changing dependencies, then `pdm sync -G dev`.
- Resolve benchmark-systems from PyPI using the version requirements in pyproject.toml. Do not introduce Git resolution overrides or workstation-specific paths into distribution requirements.
- The `replay` extra supplies Matplotlib through `benchmark-systems[anim]` and Pillow. Include it with `pdm sync -G dev -G replay` when testing GIF generation.

## Package Boundaries
- Source lives under `src/bs_simulators`; examples consume the package.
- `runtime` exposes one model-independent Simulation. Numerical integration belongs to ODEStepper or DAEStepper through the structural Stepper protocol. Omitting stepper creates a fresh RK45 ODE stepper per run; DAE selection remains explicit. Optional catalogue TOML data configures models without subclassing Simulation. Users can override physical parameters and initial data for every run. It owns the clock, ZOH, controller selection and signal descriptions. It must not import `comms` or `viewers`.
- `cli` owns the Typer terminal application and execution lifecycle. Expose model-independent options only: x0/u0 are complete vectors and physical parameters use repeated --parameter; do not add model-specific flags. Keep runtime imports lazy so --help does not initialize scientific or HTTP components.
- `comms` owns transport adapters and HTTP. Network I/O must stay outside the physics worker.
- `viewers` owns packaged browser assets. Load them with `importlib.resources`, not a checkout-relative path.
- Import models and offline animations from `benchmark-systems`; never copy their implementation into this package.
- Preserve existing offline workflows. Expose live simulation as the additional workflow; stepping remains internal.
- Describe interfaces as Python protocols. Avoid introducing a public manual-stepping mode.
- OPC UA and Modbus are future bridges; do not claim physical PLC validation until it exists.

## Verification
- Keep docs/ focused on final-user guides and API reference; remove partial reports instead of adding session history.
- Install docs tooling with pdm sync -G dev -G docs. Build with pdm run docs-build, execute examples with pdm run docs-test, and preview with pdm run docs-serve.
- After documentation dependency changes, regenerate docs/requirements.txt with pdm export --no-default -G docs --without-hashes -o docs/requirements.txt. Keep all documentation output under ignored docs/_build/.
- Write public Markdown documentation and notebook explanations in English for end users. Keep conversation notes and implementation-session history out of public guides.
- Run `pdm run pytest` and `pdm run python -c "import bs_simulators; import bs_simulators.runtime"`.
- Preserve evidence for ZOH, interval boundaries, controller ownership, indefinite input retention and monotonic receipt timestamps, independent structural engines, real subprocess HTTP and viewer resource loading.
- Build a wheel and sdist with PDM; verify the wheel contains `viewers/pendulum.html` and catalogue TOML resources and test imports/HTTP from an installed wheel outside the source checkout.
- For visual changes, provide a preview and preserve the simple Matplotlib-style scene.
- Keep public exports and runtime/package versions synchronized. Validate non-pendulum models, multi-input/zero-input plants, nonzero initial time, and atomic ODE/DAE failures. DAE consistency currently supports index at most one; fixed_x identifies states preserved during input-boundary corrections.
