# SmartMultiView

Mayaの `SmartMenu > Camera > SmartMultiView` から起動する。
既存プロジェクトのメニュー設定にも起動項目を補完する。

- 標準の `persp / top / front / side` とstartup cameraを除いたカメラを一覧表示する。
  `persp1`、参照カメラ、namespace付きカメラは対象。DAGフルパスで同名カメラを区別する。
- Columnsで1〜6列に変更できる。ウィンドウサイズに合わせて各ビューが伸縮する。
- カメラ追加・削除・名前変更の後は Refresh Cameras を押す。シーンの新規作成・読み込み時は自動更新する。
- Texture、Use All Lights、Wireframe on Shaded、Gridはこのツール内の全ビューに適用する。
  Use All Lightsを解除するとDefault Lightingに戻る。描画設定はRefresh後も維持する。
- Geometry Onlyはポリゴン・NURBSサーフェス・Subdivを表示し、curve、locator、joint、
  カメラ・ライトのアイコン、補助表示、イメージプレーンなどを非表示にする。
  OFFで各ビューの切替前の表示設定に戻す。列数変更・Refresh後もON/OFFは維持する。
  プラグイン形状の表示設定は維持する（USDなど）。シーンのvisibilityは変更しない。
  SmartGateGuideもlocatorなのでON中はこのビューでは非表示となる。
- SmartGateGuide (Scene) はシーン全体のガイド表示を変更する。通常のMayaビューにも反映される。
  On時にガイドがなければ既存プラグインで作成する。既存ガイドの文字や構図設定は維持する。
  変更はシーンに残り、ウィンドウを閉じても元には戻さない。
- 閉じる・再起動・Refresh時に、このツールが作成したmodelPanelを削除する。

## Maya内の動作確認

1. 追加カメラ5台（namespace付き、別グループ内の同名カメラも含む）で起動し、3列×2行になることを確認。
2. 各描画チェック、列数変更、ウィンドウリサイズ、タイムラインの再生を確認。
3. ガイドがないシーンでOn、既存ガイドのOff/On、ロックされたvisibilityの警告を確認。
4. カメラ追加・削除後のRefresh、シーン切替、繰り返し起動・終了で不要なpanelが残らないことを確認。

自動テストはカメラ抽出、描画設定の対象範囲、panel破棄、ガイド制御、メニュー補完を検証する。
実際のViewport 2.0の表示と操作はMaya GUIでの確認が必要。
