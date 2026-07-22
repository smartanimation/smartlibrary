# Smart Review UI Draft

目的: 既存の review / RV 起動ツールを、Asset / Shot / Light-Comp の用途別に「どの素材を、どの表示モードで、どのRVセッションへ送るか」を組み立てるツールへ拡張する。

UIイメージ: [docs/ui-mocks/smart-review-ui.svg](ui-mocks/smart-review-ui.svg)

## 基本構成

- 左: Scope と Context。`Asset` / `Shot` / `Light-Comp` を切り替え、対象アセットまたはショットを選ぶ。
- 中央: Selection と Preview Composer。variant / department / version / take / RV layout を変更し、選んだモードの表示内容を確認する。
- 右: RV Targets と Payload。送信先RV、送るソース、HUD情報、RV layout、送信方法を確認して `Send Selected to RV` する。

## Asset

| Mode | 内容 | RVへ送る情報 |
| --- | --- | --- |
| Quick Check Grid | quick check で出力した `beauty` / `wireframe` / `bbox` をグリッド表示 | 3ソース、asset名、variant、dept、version、take、frame range |
| Model Turntable | modelチェック用ターンテーブル | model turntable movまたはimage sequence、model publish情報 |
| Look Turntable | lookチェック用ターンテーブル | look turntable movまたはimage sequence、shader/look version情報 |

## Shot

| Mode | 内容 | RVへ送る情報 |
| --- | --- | --- |
| Dept Grid | `story reel` / `layout` / `animation` / `comp` のグリッド表示 | 各deptの最新または指定version、shot metadata |
| Editorial OTIO Replace | 編集OTIOの該当クリップをdept出力動画で差し替え | 入力OTIO、差し替えdept、出力OTIO、差し替え結果mov |
| Handle Trim Stitch | 選択ショットのハンドルフレームを削って繋げた表示 | shot list、handle in/out、trim後range、stitch movie |
| AOV Grid | そのショットで出力したAOVをグリッド表示 | beauty / diffuse / spec / z / crypto などのAOVソース |

## Light / Comp

| Mode | 内容 | RVへ送る情報 |
| --- | --- | --- |
| Contact Sheet | ライティング / comp チェック用のコンタクトシート表示 | selected shots、dept、version、thumbnail/contact sheet、source list |

## 実装メモ

- 現状の `scripts/viewer_ui.py` は `Open Package in RV` / `Open Layer in RV` が中心なので、新UIでは `rv_args_for_*` をモード別に組み立てる層を追加する。
- アセット側は `scripts/asset_manager_ui.py` の Preview タブと接続し、quick preview / turntable の `review.json` を Smart Review に渡せるようにする。
- Shot側は review package の `metadata/review.json` に加えて、OTIO差し替え結果、handle trim結果、AOV manifest を同じ Payload として扱う。
- RV送信は既存と同じく `rvpush merge` を基本にし、送信先RVを選べるプリセットとして扱う。
