OpenUSD tool wrappers for SmartPipeline.

The wrappers in this folder prefer the NVIDIA OpenUSD distribution extracted to:
P:/dev/smarttools/usd/nvidia-25.08

If the NVIDIA distribution is missing, usdview falls back to the USD tools bundled
with the configured Houdini installation.

Default Houdini root:
C:/Program Files/Side Effects Software/Houdini 21.0.440

Tools:
- usdcat.bat
- usdview.bat
- usdpython.bat

Python pxr:
- P:/dev/smarttools/python has usd-core for lightweight CLI tech checks.
- tools/usd/usdpython.bat uses the NVIDIA bundled Python/OpenUSD 25.08.
