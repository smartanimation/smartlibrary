# SmartPipeline Repository Rules

## Review Architecture

Review関連実装の正本は[`docs/architecture/review-artifact-lifecycle.md`](docs/architecture/review-artifact-lifecycle.md)とする。

- `render`: AE Build前のCHA、BGAなどのレイヤー素材。
- `review/review_build`: PreCompを使用した再合成可能なReview Build結果。
- `output`: InternalまたはClientへ実際に提出した成果物のみ。
- `publish/precomp`: 承認済みPreComp Project。
- `PreComp`: CHA、BGAなどのPreCompを内包し、Scale、Position、構図を確定したReview Project AEP。
- Playblast SettingsにScale、Positionまたは確定した構図を保存しない。
- 初回AE Buildは素材を中央へ配置する。
- Scale、Positionおよび構図の確定はPreCompタスクが所有する。

## Path Resolution

- パイプラインパスはすべて既存の共通Path Resolverから取得する。
- アプリ、DCC UI、Worker、Service、AE、RV内でパス階層を直接組み立てない。
- 機能コードからConfigのパステンプレートを直接展開しない。
- Manifestへ記録するパスもResolverの結果から生成する。
- Legacyパス対応はResolverまたは専用の互換Resolver内に限定する。
- 既存Resolverの設定は完了済みとして扱い、明示的な変更要求なしに重複Resolverを追加しない。

## Conflict Handling

依頼内容が上記規則または正本のアーキテクチャ文書と矛盾する場合、実装前に矛盾点と影響範囲を説明して確認する。

