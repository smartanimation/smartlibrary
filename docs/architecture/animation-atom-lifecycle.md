# Animation ATOM Lifecycle

## Canonical payload

Animation Curve の正本は Maya ATOM `animation.atom` とする。JSON Curve
`animation_curve.json` は既存データを適用するための読み込み互換形式であり、新規の
Shot Manager export、cache publish dependency、validation package では生成しない。

各バージョンは共通 Path Resolver が返す Shot Data root の次の構成を使用する。

```text
data/animation/<cast_key>/curves/v###/
  animation.atom
  animation_manifest.json
  data.json
```

`latest.json` は `v###/animation_manifest.json` を指す。

現行 schema は `smartpipeline.animation_atom.v3` とする。v1/v2 はクリーンRigおよび
namespace変更時の再構築保証がないため適用を拒否し、v3での再publishを要求する。

## Transfer contract

同一 Rig Version 間の完全一致を対象とする。`allRigSet` 自体は変更しない。
export 時の transfer node は次の和集合を実行時に解決する。

- `<namespace>:allRigSet` から解決した controller
- 同じ namespace 内で直接 animCurve が接続された transform / joint

このRigでは肩、手首、Finger等の内部 `A_*` transformにもauthored animationが存在する。
これらはクリーンRig再構築に必要なため含める。直接animCurveを持たないconstraint、camera、
他namespaceのノードは含めない。
静的な controller の keyable / channel-box 属性は manifest の `static_values` にも明示保存し、
ATOM import 後に厳密復元する。export 中に作る ATOM 用の一時定数キーは export 後に undo
するため、source scene は保存・変更しない。

## Validation and apply

`animation_manifest.json` は source namespace、transfer node 一覧、frame range、
`animation.atom` の SHA-256 を保持する。apply は checksum と全 transfer node の
namespace remap を検証し、欠落が一つでもあれば import 前に失敗させる。
Animation Layer は BaseAnimation に統合されていない限り export を拒否する。

Rig Version が異なる場合の意味的 remap はこの契約の保証範囲外とする。
