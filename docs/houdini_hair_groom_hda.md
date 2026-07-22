# Smart Hair Groom HDA

`smart::hair_groom::1.0` is the first Houdini-side hair definition HDA.
It is generated from:

```python
from smartlib.dcc.houdini import smart_menu
smart_menu.create_hair_groom_hda()
```

The generated asset is written to:

```text
packages/smartlib/dcc/houdini/otls/smart_hair_groom.hda
```

## Input

Connect guide curves to input 1. Each primitive is treated as one guide.
If a primitive already has `guide_id` or `id`, that value is used as the
stable guide identifier. Otherwise the primitive number is used.

## Output Modes

- `Render Curves`: child hair curves for Karma, with `width`, IDs, and material attributes.
- `Maya Proxy Tubes`: low-resolution polygon tubes intended for animation blocking in Maya.
- `Debug IDs`: render curves with clump-based `Cd` colors.

## Core Attributes

The HDA writes these IDs on generated geometry:

- `hair_id`
- `guide_id`
- `clump_id`
- `strand_id`
- `material_id`
- `material_name`
- `shop_materialpath`

Render curves also write point `width`, `pscale`, and `curveu`.

## First Workflow

1. Create or import guide curves in Houdini.
2. Run `Create Hair Groom HDA` from the SmartPipeline shelf.
3. Drop `smart::hair_groom::1.0` in SOPs.
4. Use `Render Curves` for Karma look work.
5. Use `Maya Proxy Tubes` when exporting lightweight animation geometry.

Keep guide IDs stable before caching or publishing. If `guide_id` changes,
the generated strand IDs and clump assignments will change too.
