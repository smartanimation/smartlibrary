# Smart Camera Playblast — Experimental v0.8

## 現行方式：Unbaked Primary + Live Output Rules

v0.8では日常PlayblastとCamera Package PublishからBakeを除去した。
以下の旧版仕様よりも、この節を現行仕様の正本とする。

- Primaryはそのまま共用する。Frame Rangeや素材の違いだけではCameraを複製しない。
- `Final output size`を構図の基準にする。
- Layerの`Output rule`は`Use Primary — shared`（初期値）、`Expand by scale`、
  `Expand by material resolution`から選択する。
- `Expansion factor`は縦横それぞれの拡張率。1920×1080・1.1倍なら2112×1188。
  端数は各辺を最も近い整数へ丸める。中心と投影ピクセル密度を維持して余白を増やす。
- `Apply Live Camera Rules`はMaya標準DGノードによる追従設定を作るだけで、時間を走査せずキーも作らない。
- Playblast前にも設定の差分確認だけを行う。設定が同じ既存ライブCameraは再作成しない。
- 拡張Camera名は`smartCam_CHA`などを維持し、Burn-inのCamera/Lens情報はPrimaryを参照する。

Camera Package v2 (`smartpipeline.camera_package.v2`) は`primary_cam.ma`とLayerの差分ルールを保存する。
Primaryの親階層・上流アニメーション・Constraint等の接続をネイティブ出力し、無関係な兄弟ノードは含めない。
参照リグは必要なノードを埋め込んで公開する。元の参照ファイルへのライブ依存は残さない。
ネイティブ出力成功後にのみcamera.json / publish.json / latestを登録する。
BuildとDataタブのApplyはPrimaryを`smartPrimary`名前空間へ読み込み、Layerごとのライブ設定を再構築する。
Dataタブでの確認とBuild ManagerのSelected Version固定は引き続き利用できる。

現在の制約：Perspective・square pixels・標準Maya依存ノードを対象とする。
外部キャッシュ、Plugin依存、動的getAttr/eval等のExpressionは公開を拒否する。
元の時間依存を維持するため、非ゼロのBuild Frame Offsetは未対応として停止する。
ネイティブ出力自体は現在のMayaで同期実行する（バックグラウンドジョブ化は未実装）。
Publish後、別mayapyプロセスがPrimaryを親なしの`primary_cam`へWorld Bakeし、
`primary_cam.usd`と`primary_cam.fbx`を同じPublish Versionへ追加する。
Bake WorkerはReview Buildと同じMaya Software設定およびProcess Environmentを使用する。
Authoring Mayaより古いWorkerしか解決できない場合は、Version作成前にPublishを停止する。
Bakeは有効Layer範囲の最小Frameから最大Frameまで、整数Frameをstep 1でサンプリングする。
交換ファイルの状態は`portable_export`の`pending / complete / failed`で確認する。

検証：`tools/maya/validate_camera_live.py`（Maya standalone専用）、`tests/test_camera_live.py`。

---

## 旧版（v0.7以前）の仕様と互換読み込み

以下は旧Bake方式の記録。旧v1 Packageの読み込みは残すが、v0.8からの新規Publishはv2を使用する。

既存Smart Playblastを残し、そのWindowクラスを継承する検証用の別ツール。
Playblast Preset、Review Layers/Camera Sequencerからの生成、行の並べ替え、
Version/Take、Output Override、連番生成、Receipt記録は既存実装を再利用する。

## 起動

MayaのPythonタブで実行する。

```python
import importlib
from smartlib.dcc.maya import smart_menu
importlib.reload(smart_menu)
smart_menu.show_smart_camera_playblast()
```

メニューを再構築する場合は `smart_menu.reload_smart_menu()` を実行する。
`SmartMenu > Render > Smart Camera Playblast (Experimental)` が追加される。
既存の `Smart Playblast` は残る。

SmartViewportGateGuideが既にロードされている場合、新しいBurn-in処理を使用するには
シーンを保存してMayaを再起動する。ノードを使用中のプラグインを強制Unloadしない。

## 検証手順

Review Layersはカード型ListView。カード選択で右のPropertiesへ連動し、
左のチェックで出力対象を切り替え、カードをドラッグしてLayer順を変更する。
上部のボタン群は廃止し、Refresh/Import/選択操作/Version/Takeなどは右クリックメニューから実行する。
既存Smart Playblastの行モデルを共有しているため、設定保存と出力処理は従来どおり。

1. 作業シーンのコピーで試す。元のSmart Playblastウィンドウは閉じておく。
2. Primary Cameraを選ぶ。Reference SizeにはPrimaryの基準解像度を指定する。
3. 左の各行に従来どおりResolution、Frame Range、Preset、Version/Takeを設定する。
4. 各行を選択してFit Policyを指定する。
5. 生成対象の行だけチェックし、Generate / Update Camerasを押す。
6. 各行のCamera欄に `smartCam_CHA`、`smartCam_BGA` などが設定されたことを確認する。
7. Playblast Image Sequenceを実行し、PreCompで素材の重なりを確認する。

特殊な別カメラを使用するLayerは生成時にチェックを外し、既存Camera欄から個別に指定する。
Playblast時はチェックを戻す。自動更新は管理タグの付いた生成カメラだけを対象にする。

**行設定と出力先は既存Smart Playblastと共通**。別ツールだから出力先が隔離されるわけではない。
連番の上書きを避けるには新しいTake/Versionを使用する。Primary/基準解像度/Fit Policyのみ
専用network node `smartCameraPlayblastInfo` に保存する。シーン保存で永続化される。

## Fit Policy

| Policy | 意味 |
| --- | --- |
| Preserve Horizontal | Primaryの水平画角を保持し、垂直範囲を出力比率に合わせる |
| Preserve Vertical | Primaryの垂直画角を保持し、水平範囲を出力比率に合わせる |
| Fit | Primaryの範囲全体を含めるように片方の範囲を広げる |
| Fill | 出力比率に合わせてPrimaryの片方の範囲を切り詰める |
| Keep pixel scale | 被写体のピクセルサイズを保持し、解像度に比例して撮影範囲を拡張/縮小する |

単に精細度を上げる場合はHorizontal/Vertical。同じピクセル密度で大きなBGA素材などを
出したい場合はKeep pixel scaleを使う。Fitは黒帯の画像を生成する機能ではなく、撮影範囲の拡張。
異なるアスペクト比で水平・垂直両方のFrustumを同時に保持することはできない。

## カメラの生成方式

- PrimaryのWorld Transform、焦点距離、Film Fitを含む投影を各フレームで評価する。
- `MFnCamera.getRenderingFrustum(referenceAspect)` から出力用Gateを解決する。
- 焦点距離はPrimaryと同じ値を維持し、Film Aperture/Offsetを計算する。
- 新規出力カメラはワールド直下、Film Fit=Horizontal、Overscan=1、Camera Scale=1に正規化する。
- 各Layerの整数Frame RangeでTransformとCamera Shapeを毎フレームBakeする。
- 各サンプルで生成カメラの投影・World Matrixを数値照合する。
- 生成カメラの管理タグとPrimaryへのmessage接続を使って更新対象を特定する。
- 同名の他カメラを上書きしない。再生成は管理カメラのキーだけを置き換える。
- 旧ID付きの管理カメラはGenerate / Update時にIDなしの名前へ変更する。
  同名ノードや別Primaryの同名カメラがある場合は停止し、勝手に連番を付けたり上書きしたりしない。
- エラー/キャンセル時は生成トランザクションをUndoし、時刻と選択を復元する。

Maya APIの投影定義は[Autodesk MFnCamera documentation](https://help.autodesk.com/cloudhelp/2025/ENU/MAYA-API-REF/py_ref/class_open_maya_1_1_m_fn_camera.html)を参照。

Update managed cameras before Playblastは初期状態でON。Primaryの変更やFrame Rangeの変更を
反映してから既存Playblast処理を実行する。OFFの場合は最後にBakeした状態を使用する。

## Camera Package Publish / Build

`Publish Camera Package...` は既存のShot Camera Publishサービスと共通Path Resolverを使用する。
このツール独自のPublishディレクトリやPath Resolverは持たない。

### DataタブからBuildへ

1. Shot ManagerでShotを選択し、Dataタブを更新する。
2. Camera以下のTarget / Subsetに、`Camera Package`表示のPublish Versionが並ぶ。
   VersionをダブルクリックするとPrimary、Reference Resolution、LayerごとのCamera / Resolution /
   Frame Range / Fit Policy / Version / TakeとPublishパスを確認できる。
3. Build ManagerのBuild Contentsで`Camera Package / <target> / <subset>`行を探す。
4. `Selected Version`のドロップダウンから使用するVersionを明示的に選択する。
   同じVersionを選び直した場合も選択を保存する。他のCamera Package行は自動でOFFになる。
5. 選択したVersionとパスはConstructとPlanned Snapshotに引き継がれる。
   新しいPublishが増えても選択済みVersionは変更しない。別Versionへ切り替えるときは再選択する。

DataタブはPublish済みファイルを閲覧するだけで、Data領域へ複製しない。
既存の旧Camera DataとCamera Packageの競合・移行はこの導線の対象外。
コード更新後はShot ManagerとBuild Managerを再起動してから確認する。

- Publish前に`Generate / Update Cameras`を実行する。
- Publish対象はPrimary、Reference Resolution、Layerごとの
  Resolution / Frame Range / Fit Policy / Version / Take。
- Schemaは`smartpipeline.camera_package.v2`。Primaryは論理キー`primary`で常に1つとする。
  Maya UUIDとfull DAG pathはBuild間の識別子として保存しない。
- Camera名は`smartCam_CHA`のような表示名を維持する。識別用IDを名前へ追加しない。
- Maya Build用には非Bakeの`primary_cam.ma`とLayer差分ルールを保存する。
  DCC交換用にはWorld Bake済みの`primary_cam.usd`と`primary_cam.fbx`を保存する。
- source rig、Constraint、Render Layer素材、AEのScale / Position / Anchor / 構図は含めない。
- 検証の不一致、古い生成設定、同名Node、単位不一致、別Primary由来のCameraは停止する。
  同名Nodeを削除、上書き、自動採番しない。

Buildは選択されたCamera Publishを読み、`camera_grp|smartCameraPublish`以下にPrimaryと生成カメラを
再構築する。各Cameraへrole/key属性を付け、生成カメラからPrimaryへmessage接続を復元する。
Playblast rowsと生成Policyも同時に復元するため、Publish前のfull pathやMaya UUIDには依存しない。
Build後は公開結果を維持するためAuto-updateをOFFにする。変更が必要ならPrimaryを編集して
Generate / Updateした結果を新VersionとしてPublishする。
公開済みPrimaryのサンプル範囲を超えるGenerateと、小数フレームのBuild Offsetは拒否する。

同時に使用できるCamera Packageは一つ。既存の`smartCameraPublish`または公開済み設定がある場合は
Buildを停止する。複数Camera Publishの合成や、同名Cameraの推測による割当は行わない。

このCamera PackageはMaya Shot Build入力であり、AE Buildの`render_manifest.json`ではない。
既存Render Manifestの責務と、Mayaシーン内Playblast SettingsをAE Build入力にしない規則は変更しない。
FBX/USDはCamera Package Publish後のバックグラウンド処理で生成する。

## Burn-inと交換

SmartViewportGateGuideの `{camera}` / `{camera_clean}` / `{focal_length}` は、
管理カメラについてのみPrimaryを参照する。通常カメラの表示は変更しない。
`{output_camera}` / `{output_focal_length}` は実際の出力カメラを表示する。
生成カメラのゲートガイドはそのLayerの出力解像度を使用する。

出力カメラは通常のMayaカメラとキーなので、既存FBX/USD Exportへ渡せる。
このツールにはファイルExportボタンを追加していない。FBX/USD受け渡し後のFrustumと
Film Offsetの再現は受け側DCC/Importerでも確認すること。クロスDCCのround-trip保証は未実施。

## 検証版の制約

- Perspective / square pixels / integer frame samplingのみ。
- Orthographic、2D Pan Zoom、Film Roll、Film Translate、非デフォルトPre/Post Scaleは明示的に拒否する。
- サブフレーム/Motion Blur一致は保証しない。
- 生成カメラは親階層内でも更新できるが、管理カメラ自体は手修正せずPrimaryを編集する。
- 生成カメラへの参照リンクはBurn-in用。Transform/Projectionのライブリンクではない。
- AEのScale/Position/Anchor/構図確定を設定しない。Render ManifestやResolverを追加・変更しない。

## 自動検証

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_camera_output.py
.\.venv\Scripts\python.exe -m pytest tests/test_camera_publish.py tests/test_shot_builder_camera.py tests/test_shot_construct.py
& 'C:\Program Files\Autodesk\Maya2026\bin\mayapy.exe' tools/maya/validate_camera_playblast.py
& 'C:\Program Files\Autodesk\Maya2026\bin\mayapy.exe' tools/maya/validate_camera_playblast_ui.py
& 'C:\Program Files\Autodesk\Maya2026\bin\mayapy.exe' tools/maya/validate_camera_publish.py
```

Mayaスクリプトは独立したmayapyプロセス専用。開いているアーティストのMayaセッション内では実行しない。
Core検証はMaya 2024/2026、UI smokeはMaya 2026で実行済み。
UI smokeはShot Serviceをfixtureに置き換え、実Projectデータへ書き込まずに起動・割当・設定を検証する。
実際の素材連番とPreComp合成の目視確認は、作業シーンのコピーで行う。
