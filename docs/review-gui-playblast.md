# Review専用GUI Maya Playblast

`maya_playblast`プロファイル（`work_default`など）のReview素材生成は、
mayapyで構築したシーンを専用GUI Mayaへ渡して行う。
アーティストが起動しているMayaには接続しない。

- シーン構築・Snapshot解決は既存Workerのまま。
- 構築済みの現在状態をJobサイドカーへExport Allする。元のMA/MB形式と
  Referenceを保持し、作業ファイルや構築済みSourceを上書きしない。
- 1ショットのキャッシュMISS Layerを1回のGUI起動で順番に出力する。
  全LayerがHITならGUIを起動しない。
- GUI内では再構築・Rig再解決・Animation Curve再適用をしない。
  指定されたフルDAGパスのCamera、Display Layer、尺、解像度、Overscan、
  Render Manifestが指定するPresetを適用する。
- MayaバージョンはWorkerのmayapyと同じインストールを使用する。
  個人設定に依存しない一時MAYA_APP_DIRを使い、必要なプラグインと
  パイプライン環境を引き継ぐ。Color Managementは構築済みシーンの設定を使う。
- 出力後に画像の読込と解像度を検証し、全Layer成功後にキャッシュを確定する。
  失敗時もキャッシュロックを解放する。旧OGSキャッシュは再利用しない。

## 制約と運用

- Windowsの対話デスクトップとGPUが必要。完全なヘッドレスサービス、
  ロック画面、RDP切断時の安定動作は保証しない。
- 専用プロセスは非表示起動を要求するが、Mayaがウィンドウを表示する場合がある。
  ビューポート描画のためのGUIであり、作業中Mayaを最小化する必要はない。
- GUI PlayblastはPNG/JPEGの8-bit画像を対象とする。PNGの透過は保持する。
  明示的な`maya_render`プロファイル（`rend_default`のEXR等）は既存描画経路を維持する。
  GUI側でEXRを要求した場合はエラーにし、PNGやOGSへ黙って切り替えない。
- 同じManagerのJob Queueは既存の逐次実行を使用する。
  別Managerや別ホストも含めたGPU同時実行の全体制御は追加していない。
- Job DetailsにGUI起動・シーン読込・Layer/Frame進捗と失敗理由を表示する。
  Jobの`.playblast.request.json`、`.playblast.result.json`、`.playblast.log`、
  `.playblast.console.log`と描画用シーンは調査用に残る。
- 起動・進捗停止のタイムアウト時は、このJobが起動したMayaのみ終了する。
  OSからWorkerを強制終了した場合の子プロセス回収は保証しない。

プロジェクトの`review.yml`で変更できる設定：

```yaml
gui_playblast:
  startup_timeout_seconds: 180
  stall_timeout_seconds: 300
```

## 実機検証

`tools/maya/validate_gui_review_playblast.py`を設定済みmayapyで実行する。
`--scene`、`--camera`、`--layers`、`--frame`、必要なら`--plugin`を渡す。
成果物は新規TEMPフォルダへ出力し、元シーンは保存しない。

c002の構築済みv003で、CHA/BGAの630–631f（640×360、計4枚）を検証。
専用Maya1回で出力・終了し、CHAの背景Alpha=0、JINの肌・髪の黒化解消を確認。
起動・読込・出力・終了込みの参考実測は約24秒。
これは全尺やAE合成を含めた所要時間ではない。
