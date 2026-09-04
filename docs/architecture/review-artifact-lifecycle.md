# Review Artifact Lifecycle

- Status: Accepted
- Decision date: 2026-09-01
- Scope: SmartPipelineのShot Review、Smart Playblast、Smart AE Browser、PreComp、Review Build

この文書は、SmartPipelineにおけるReview関連成果物の名称、責務、保存領域、状態遷移に関する正本である。

## 用語

### Render Layer Material

Mayaから生成される、AE Build前のレイヤー別連番素材。CHA、BGAなどを含む。

- 合成前の素材であり、Review提出物ではない。
- 素材が確定する前から生成できる。
- Smart AE BrowserのReplace Queueが更新対象として参照する。
- `output`には保存しない。

### Render Manifest

Maya、Houdini、BlenderなどのDCCに依存しないAE Build入力Manifest。
標準ファイル名は`render_manifest.json`、Schemaは
`smartpipeline.render_manifest.v1`とする。

次の情報を所有する。

- 使用するReview Layer
- Camera
- Frame Range
- 素材解像度
- Layer Order
- VersionおよびTake
- Render Layer Materialの連番参照
- Playblast Presetなど、素材生成とAE Buildに必要な入力条件

Scale、Position、Anchor Pointおよび確定した構図は所有しない。

Smart Playblastはシーン内設定を使用してRender Layer Materialを生成し、各Layerの
生成結果を`v###/t##/output.json`へReceiptとして記録する。
Receiptは実フレーム数、解像度、先頭・末尾ファイルを所有する。
Render Layer Material、作業AEPおよびWorking Review MovieのTakeは`t##`の2桁表記とする。
旧`v###/output_t###.json`構造は読み取り互換のみ維持し、新規出力には使用しない。

Render Manifestは、必要な全LayerのReceiptと実ファイルが揃った後にのみ、
Shot Managerの`Export Data`からVersion化できる。Export時に実際のVersion、Take、
連番パターン、Receiptパスを取り込み、Settings VersionとFingerprintを各Receiptへ
関連付ける。AE BuildはManifestとすべてのReceiptが一致する場合だけ開始できる。

### PreComp

CHA、BGAなどのAE PreCompを内包し、Scale、Position、構図を確定したReview Project AEPを、SmartPipelineでは`PreComp`と定義する。

- タスク名: `PreComp`
- 成果物表示名: `PreComp Project`
- Publish Type: `precomp`
- 標準ファイル名: `precomp.aep`

AE標準用語の個別PreCompと区別が必要な文章やUIでは、`PreComp Project`と表記する。

### Review Build

Publish済みPreCompとRender Layer Materialを使用して生成する、再合成可能なReview結果。

次の成果物を含む。

- 合成済みImage Sequence
- 技術確認用Review Movie
- `review_build_manifest.json`
- 使用したPreComp、Playblast Settings、各LayerのVersionおよびTakeへの参照

Review Build内のMovieはBuild結果の技術確認用であり、それだけではInternalまたはClientへの提出物を意味しない。

### Working Review Movie

Smart AE BrowserのRenderから、現在の作業AEPを確認するために直接生成するMOV。

- Review Buildを生成しない、日常的な作業確認用の成果物である。
- `review/review_build`および`output`には保存しない。
- Shot Workspaceの`review/{department}/mov`へ保存する。
- VersionおよびTakeはディレクトリではなくファイル名で識別する。
- 保存先は共通Path Resolverの`shot_review_movie_dir`から取得する。

### Output

InternalまたはClientへ実際に提出したチェック成果物。

- 作業途中のPlayblastを保存しない。
- Render Layer Materialを保存しない。
- Review Buildの途中生成物を保存しない。
- 提出元のReview BuildをSubmission Manifestから追跡可能にする。

### Publish

工程間で再利用する承認済みの正式データ。PreComp Projectなどを含む。

`production`は処理が一時ファイルを生成する場所ではなく、承認済みまたは正式にPublishされたデータを置く領域である。

## 標準ライフサイクル

```text
Render Layer Material生成
        ↓
Render Manifestを入力として初回AE Build
        ↓
各素材を中央へ初期配置
        ↓
PreCompタスクでScale・Position・構図を確定
        ↓
PreComp ProjectをPublish
        ↓
Review Buildを生成・検証
        ↓
InternalまたはClient向け成果物をOutput
```

運用上の順序は次のとおり。

```text
Smart Playblastが連番を生成
        ↓
Layerごとにv###/t##/output.jsonを記録（complete）
        ↓
Shot ManagerでRender ManifestをExport Data
        ↓
Smart AE BrowserがSettings・Receipt・実ファイルを照合
        ↓
初回AE Build
```

`Publish Preview Render`および`preview_render_manifest`はこの運用では生成しない。
AE Buildの入力Manifestは`render_manifest.json`を正本とする。

Render Layer Materialの連番生成に成功しただけでは、Review素材としての成功とは判定しない。キャラクターと背景の解像度が異なる場合があるため、AE Build後の合成状態で解像度、構図、フレーム範囲、前後関係を確認する。

## 初回AE Build

- Build成功後は、共通Path Resolverで解決したShot Workの作業AEPへ自動的にSave Asする。
- Build元のBase AEPを作業中の保存先として使用しない。
- 作業AEPのファイル名はNaming設定に従い、既存Version/TakeとManifest Versionから次の採番を決定する。
- Shot Workは`{department}/{dcc}/{workflow_task}/{option}`の順でDCCとTaskを分離する。
- AEの標準DCC IDは`ae`、Workflow Taskは`preComp`、Optionは`main`とする。
- AEの作業ファイル名に使用するFile Taskは`compTemp`とし、Workflow Taskとは別に設定できる。
- Shot WorkファイルはVersionを3桁、Takeを2桁で表記する。
- 素材のAnchor Pointは素材中央を初期値とする。
- 素材のPositionは親Comp中央を初期値とする。
- Playblast SettingsにScaleまたはPositionの確定値を要求しない。
- Scale、Positionおよび構図の確定はPreCompタスクの責務とする。
- Render Layer Materialの更新時は、既存PreCompのTransform、Effect、Layer構造を保持してFootageをReplaceできること。

## 論理パス

以下は保存領域の意味を示す論理構造である。具体的な絶対パスは必ず共通Path Resolverから取得する。

```text
{workspace_root}/{workspace_partition}/shots/{episode}/{sequence}/{shot}/
├─ work/
│  └─ {dept}/
│     ├─ ae/
│     │  └─ preComp/
│     │     └─ main/
│     │        └─ {project}_{episode}_{sequence}_{shot}_compTemp_v001_t01.aep
│     └─ maya/
│        └─ preComp/
│           └─ main/
│              └─ {project}_{episode}_{sequence}_{shot}_preComp_v001_t01.ma
│
├─ render/
│  └─ {dept}/
│     └─ layers/
│        ├─ CHA/
│        │  └─ v001/
│        │     └─ t01/  # 連番とoutput.json
│        └─ BGA/
│           └─ v001/
│              └─ t01/  # 連番とoutput.json
│
├─ review/
│  ├─ {dept}/
│  │  └─ mov/
│  │     └─ {project}_{episode}_{sequence}_{shot}_{task}_v001_t01.mov
│  └─ review_build/
│     └─ v001/
│        └─ t001/
│           ├─ image_sequence/
│           ├─ review.mov
│           └─ review_build_manifest.json
│
└─ output/
   └─ review/
      ├─ internal/
      │  └─ v001/
      └─ client/
         └─ v001/
```

PreCompの論理Publish構造は次のとおり。

```text
{production_root}/shots/{episode}/{sequence}/{shot}/
└─ publish/
   └─ precomp/
      └─ v001/
         ├─ aftereffects/
         │  └─ precomp.aep
         └─ publish.json
```

## Path Resolver規則

パス設定および実パスの解決は、既存の共通Path Resolverを唯一の経路とする。

- アプリ、DCC UI、Worker、Service、AE、RVの機能コードでパス階層を直接組み立ててはならない。
- 機能コードからConfigのパステンプレートを直接`format`してはならない。
- Workspace、Production、Workspace Partition、Department、Audience、Version、Takeの正規化はResolverが所有する。
- AEおよびRVには、Resolverが解決してManifestへ記録したパスを渡す。
- Legacyパスの探索と変換はResolverまたは専用の互換Resolver内に限定する。
- Manifestに記録するパスも、Resolverの結果から生成する。

以下のような直接構築は禁止する。

```python
shot_root / "render" / department / "layers" / layer
Path(project_root) / "production" / "shots"
```

以下のようにResolver APIを使用する。

```python
paths.shot_render_layer_dir(identity, department, layer, version)
paths.shot_review_movie_dir(identity, department)
paths.shot_review_build_dir(identity, department, version, take)
paths.shot_review_output_dir(identity, department, audience, version)
paths.shot_precomp_publish_dir(identity, version)
```

API名は既存Resolverの実装を正とし、上記は責務を示す例である。この文書を理由に重複Resolverを新設してはならない。

## 変更管理

- この決定と矛盾する仕様変更または実装依頼を受けた場合、変更前に矛盾点と影響範囲を提示して確認する。
- 規則を変更する場合は、この文書のStatus、Decision date、該当セクションおよび移行方針を同時に更新する。
- 一時的な例外は暗黙に実装せず、理由、対象、終了条件を文書化する。
- 既存Resolverの設定は完了済みとして扱い、具体的な不具合または明示的な変更要求がない限り再設計しない。
