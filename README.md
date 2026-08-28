# smartpipeline

smartpipeline is a production pipeline toolkit for CG, animation, and full-CG workflows.

The current repository still keeps the existing Smart Launcher and Asset Manager entry points while new reusable logic is introduced under `packages/smartlib`.

## Development

```bat
set SMARTPIPELINE_ROOT=P:\dev\smartlibrary
set PYTHONPATH=%SMARTPIPELINE_ROOT%\packages;%SMARTPIPELINE_ROOT%
python -m smartlib.apps.launcher
```

During production deployment, the same package can also be exposed by `PYTHONPATH` without installing it.

### Tests

Use the repository runner so every task uses the same Python and keeps pytest
temporary data outside the repository:

```powershell
.\scripts\run_pytest.ps1
```

Arguments are passed to pytest, for example
`.\scripts\run_pytest.ps1 tests\test_output_resolver.py -q`. The runner checks
the shell, the repository `.venv` Python, and pytest in that order. Prepare a
missing environment with `py -3.12 -m venv .venv`, then
`.\.venv\Scripts\python.exe -m pip install -e ".[dev]"`.

## Architecture

- [Review Artifact Lifecycle](docs/architecture/review-artifact-lifecycle.md): Review Layer Material、Playblast Settings、PreComp、Review Build、OutputおよびPath Resolverの規則。
