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
