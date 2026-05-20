# OpenRV

Place a Windows OpenRV zip extraction here.

Expected layout:

```text
tools/OpenRV/OpenRV-{version}/
  bin/
    rv.exe
```

Configure `config/STKB/tools.yml`:

```yaml
tools:
  openrv:
    path: "P:/dev/smartlibrary/tools/OpenRV/OpenRV-{version}/bin/rv.exe"
```

The official AcademySoftwareFoundation/OpenRV GitHub releases currently do not provide Windows binary assets. Source archives are not enough for Viewer integration because they do not include `rv.exe`.
