# Studio Delivery FBX

Asset Managerの **Studio Delivery** タブで、背景・キャラクターの公開済みMaya Contextからスタジオ提供用FBXを生成する。
Pipeline Profile（maya_rend_atom等）とは独立した提供用途のContextであり、制作Contextの品質プロファイルは変更しない。

## プロジェクトで有効にする

プロジェクトのconfigディレクトリに `contexts/studio_delivery/v001.yml` を作成する。

```yaml
enabled: true
```

標準設定は `config/default/contexts/studio_delivery/v001.yml`。既存のContextレイヤー合成
（default → studio → project）を使用する。既定は無効。必要な項目だけプロジェクトで上書きする。
Context版を固定するときは、既存の `project_settings.yml` の `active_contexts` に追加する。

```yaml
active_contexts:
  studio_delivery: v001
```

キャラクターは `MCP`、背景は `REND` のpacked Maya Publishを選ぶ。
別の提供元Contextやセット名が必要ならプロジェクトYAMLで変更する。

```yaml
enabled: true
categories:
  character:
    source_context: MCP
    geometry_set: cache_geo_set
    skeleton_set: skel_export_set
    skins: true
  environment:
    source_context: REND
    geometry_set: cache_geo_set
    skins: false
fbx:
  file_version: FBX202000
  units: cm
  up_axis: y
  animation: false
```

## 操作

1. Asset Managerでアセットと制作Variantを選び、Studio Deliveryタブを開く。
2. Source publishで具体的な元バージョンを選ぶ。latest/approvedエイリアスは生成入力にしない。
3. Generate & Validate FBXで別プロセスのMayaを起動する。編集中のMayaシーンは変更しない。
4. 再読込検証に合格したFBXをValidated FBXから選び、Finalize Delivery Versionを実行する。
5. 提供版のOpen Folder / Copy FBX Pathからファイルを取得し、スタジオへ渡す。
6. 実際に送付した後にRecord Sentでスタジオ名・必要なら実送付日時をNoteへ記録する。自動送信はしない。
7. モーションを受領したら、使用した提供版を選択してRegister Received FBXを実行する。
   スタジオとテイク名を記録し、受領ファイルの独立したコピーを保持する。
8. Copy Received FBX Pathから取得したパスを、既存のDependencies / inputsまたはRetarget Setupの入力に指定する。
   このタブはショットやテイクのLO / ANIMへの割り当て・リターゲット実行を自動決定しない。

Mayaは既存の `maya_runtime.resolve_mayapy` とプロジェクトのsoftware設定で解決する。
必要なら `SMARTPIPELINE_MAYAPY` を使用できる。

## バージョンと保存領域

- 提供版はアセット単位でv001から採番する。Variant・スタジオが異なっても連番は共通。
- 社内v012 → 提供v001のように元バージョンとは独立。元Variant、Context、版、設定をdelivery.jsonへ保存する。
- 生成試行はUUIDで識別し、失敗・キャンセルでは提供番号を消費しない。
- 確定時はアセット単位の排他ロックと同一ディレクトリ内のrenameで公開する。同じ生成結果の再確定は同じ版を返す。
- 確定版を更新する操作は提供しない。参照時はFBXハッシュを照合し、外部変更を検出する。
- 送付イベントと受領イベントは提供版と分離して追記保存する。
- 受領FBX → 提供版のハッシュ → 元キャラクター版を追跡できる。

全保存先は既存ProjectPathsのstudio_delivery_path / artifact_fileから取得する。
Variantをアセット共通として解決する方法は既存Retarget Setupと同じ。
生成ジョブはAsset Work、確定版はAsset Publishのstudio_delivery、送受領記録はAsset Dataのstudio_deliveryへ保存する。
生成中FBXをoutputへは保存しない。確定版は再利用可能なPublishとして保持し、送付イベントで提供を記録する。
ロックを保持したプロセスが異常終了した場合は、実行中の確定処理がないことを確認してrelease.lockを管理者が除去する。

## FBX検証

保存済みMayaシーンのポーズを静的アセットとして書き出す。提供元を所定の基準ポーズでPublishしておく。
geometry_setにはメッシュまたはその親グループを含める。
キャラクターのskeleton_setにはスキンの全影響ジョイントと祖先ジョイントを含める。
セット名は名前空間を横断して一意である必要がある。

書き出し後にFBXを新規シーンへ読み込み、以下を照合する。

- メッシュ名・トポロジー・ワールド頂点位置
- ジョイント名・階層・ワールド姿勢
- スキンの影響ジョイントと頂点ウェイト（許容差1e-5）
- 静的アセットにアニメーションカーブが含まれないこと
- 元シーンとロード済みMayaリファレンスのハッシュ

同名メッシュ、複数skinCluster、未ロードReference、必要なセットの欠落はエラーにする。
マテリアル・テクスチャ・BlendShapeの完全な往復一致は検証対象外。
受領登録は原本のコピーとハッシュ記録であり、モーション品質や骨格適合性の検証は既存Retarget Setupのテストで行う。

## 開発検証

```powershell
$env:PYTHONPATH = 'packages;.'
.venv/Scripts/python.exe -m pytest tests/test_asset_studio_delivery.py tests/test_studio_delivery_ui.py --basetemp=.tmp/pytest/studio-delivery
& 'C:/Program Files/Autodesk/Maya2024/bin/mayapy.exe' tests/maya_studio_delivery_smoke.py
```

Maya smokeは.tmp配下だけにテスト用シーンとFBXを作る。
背景・スキン付きキャラクター・名前空間付きReference（m / Z-up変換）を検証する。
