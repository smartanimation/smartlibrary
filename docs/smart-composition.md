# Smart Composition

Shot Managerで公開したUSD compositionを開き、ターゲットごとの公開済みProductバージョンを切り替えて確認するスタンドアローンツール。

## 起動

Launcherでプロジェクトを選択し、**SmartTools → Smart Composition**から起動する。選択中のプロジェクト設定が引き継がれる。

上部のProjectはLauncherで選択したプロジェクトに固定され、設定ディレクトリを入力する必要はない。
Episode → Sequence → Shot → Compositionバージョンを選択し、Openを押す。
候補はShot Managerの登録Shotと公開済みcompositionから取得する。公開compositionがないShotではOpenを無効にする。
Refresh Shotsで一覧を更新する。読み込み中のcompositionパスは選択欄の下に表示する。

`bat/run_smart_composition.bat`からの起動は`PROJECT_CONFIG_DIR`または`--config`でプロジェクトを指定する。
起動引数にcompositionフォルダを渡すこともできる。

```bat
bat\run_smart_composition.bat --config P:\dev\smartprojects\config\ELCD D:\Projects\ELCD\production\shots\ep02\s027\c001\publish\usd\composition\v001
```

既存の`tools/usd/usdpython.bat`とNVIDIA USDランタイムを使用する。
通常の`usd-core`のみのPython環境にはHydra/Usdviewqが含まれないため、このバッチで起動する。

## 操作

- Animation / Assets / Camera / Layout内のターゲットのチェックとバージョンを変更すると、自動的にUSDプレビューを更新する。
- Playとフレームスライダーでアニメーションを確認する。Free camera／公開Cameraを選択できる。
- Frame Allで全体をフレーミングする。
- Refresh Versionsは新しく公開された候補を追加し、現在の選択は維持する。
- Resetは最後に読み込んだ／保存した選択へ戻す。
- Save New Compositionは既存サービスの共通Resolverで新しいcompositionバージョンを予約し、`shot.usda`、4つのレイヤー、参照元を固定したmanifestを保存する。

## 契約と制限

プレビューは匿名USDレイヤーだけを作成し、プロジェクトへファイルを書き出さない。
保存は既存のUsdHandoffService.compose_productsを使用し、公開済み入力を変更しない。
プレビューと保存は共通の検証・合成処理を使う。異なるShot、タイミング、単位、重複ターゲット、複数Primary Camera、欠損・変更済み依存を拒否する。
Saveはプレビュー済みの選択のみ受け付け、Product manifestの変更も再検証する。
成果物はpartial / not_reviewedであり、Review/PreCompの承認操作ではない。

対応入力はShot Managerの`smartpipeline.usd_handoff_composition.v1`とProduct manifest。
任意のUSDレイヤー編集、FX/Lighting、マテリアル編集は対象外。
切り替えは非同期で自動更新するが、大きなUSDや依存チェックには読み込み時間がかかる。固定FPSでの再生速度は保証しない。

## 検証

`tests/test_smart_composition.py`で実USDのバージョン切替、無効化、元データ保持、新規保存、再読込、入力改変拒否を検証する。
既存`tests/test_usd_handoff.py`も回帰対象。
描画にはOpenUSDの[StageView](https://github.com/PixarAnimationStudios/OpenUSD/blob/dev/pxr/usdImaging/usdviewq/stageView.py)を使用する。

## カメラ表示

- Cameraリストから公開カメラを選び、Camera Maskで枠外の半透明ブラックをOn／Offできる。Free cameraではマスクを無効にする。
- Shot / Camera Infoでショット名、プロジェクト解像度、フレーム、FPS、カメラ名、焦点距離、カメラ枠の比率を表示／非表示にできる。
- 解像度はプロジェクト設定のanchors.resolutionを表示する。USDカメラの枠はapertureに従い、プロジェクト解像度の比率とは別に表示する。
- 焦点距離は現在フレームのUSD値をmm換算する。ズームアニメーションにも追従する。換算は[OpenUSDのカメラ単位仕様](https://openusd.org/dev/api/class_usd_geom_camera.html)に従う。
- マスクと情報表示はビュー専用で、USDや保存するcompositionを変更しない。

## レイヤーツリー

親行はAnimation／Assets／Camera／LayoutとSectionバージョン、子行は実素材名とShot Productバージョンの2段で表示する。親のバージョンを変更すると、そのSectionの素材構成を読み込む。子のチェックとバージョンを変更するとプレビューに反映する。親チェックは子素材をまとめて切り替える。

子の構成が親Sectionの記録と異なる場合は親名に`*`を表示する。Saveは現在の子素材構成で新しいcompositionを保存し、対応するSectionを作成／再利用して表示を更新する。Resetは最後に開いた／保存した構成へ戻す。子行の番号はShot Productバージョンであり、参照先のAsset PackやCamera Publishの番号とは別。参照先の実バージョンとフルパスは下部の詳細欄で確認できる。

Version列の右にLatest列を表示し、各Section／Productの最新公開バージョンを確認できる。Refresh Versionsで更新する。Latestは表示専用で、現在の選択を自動変更しない。

## USDチェック動画の保存規約

Workspaceの`review/{department}/usd/mov`を使用する。共通Resolverは`shot_review_movie_dir(..., review_kind="usd")`。
例: `ELCD_ep02_s027_c001_usd_v001_t01.mov`と`ELCD_ep02_s027_c001_usd_v001_t01_report.pdf`。
AE仮組み動画は`review/{department}/compTemp/mov`へ分離する。既存の動画は移動しない。

### 動画と参照レポートの出力

1. Compositionを開き、レイヤー構成を確認する。変更した場合はSave New Compositionで保存する。
2. Cameraリストから公開カメラを選ぶ。Free cameraでは動画を書き出せない。
3. **Create Review Movie + PDF**を押す。進捗画面からキャンセルできる。
4. 完了後、**Open Output**で保存先を開く。

保存済みcompositionの全フレームを、プロジェクト設定の解像度、USDのFPSで描画する。動画は音声なしのH.264 MOV。カメラ枠内を出力し、縦横比が出力解像度と異なる場合は余白を付けて保持する。画面のHUDや枠外マスクは動画へ焼き込まない。

動画の番号は出力先で自動採番し、compositionの番号とは独立する。既存動画を上書きしない。部門は現在animを使用する。

同じ名前の`_report.pdf`をA4の1ページで出力する。ショット、フレーム範囲、FPS、解像度、カメラ、先頭フレームの焦点距離、代表画像と、composition・Section・Product・参照USDのファイル名およびバージョンをまとめる。PDFにフルパスやハッシュは表示しない。Productバージョンと参照USDのバージョンは列を分ける。同名の`.json`にフルパスを含む入力固定情報と出力ハッシュを保存する。これらは未承認のチェック成果物であり、outputやPreComp承認には登録しない。

出力前後で入力の整合性を検証し、FFprobeでMOVの解像度・FPS・フレーム数を確認する。FFmpegと同じディレクトリにFFprobeが必要。失敗・キャンセル時は作成途中の最終成果物を残さず、診断用の作業ファイルを保持する。

`tests/test_usd_review_export.py`で保存先、重複しない採番、依存改変拒否、カメラ検証を確認する。描画はUSDランタイムによる実出力で別途確認する。

カメラの子行は`smartCam_CHA (primary_cam.usd)`のようにターゲット名と実ファイル名を併記する。カメラ選択欄とHUDにもターゲット名を表示する。Layer / Targetのチェックやバージョン変更時は描画状態を再作成してプレビューを更新し、現在フレームと選択カメラ（新しい構成に存在する場合）を維持する。
