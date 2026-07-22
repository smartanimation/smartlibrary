# OpenRV

SmartPipeline OpenRV integration lives here.

External OpenRV itself should live outside the repository, for example:

```text
P:/dev/smarttools/openrv/
```

Configure the executable in studio config:

```yaml
tools:
  openrv:
    path: "C:/Program Files/Autodesk/RV-2025.1.0/bin/rv.exe"
```

SmartPipeline-owned RV plugin code is kept in:

```text
tools/openrv/smart-review/
```
