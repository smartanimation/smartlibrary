# Asset Assembly: Place Asset

背景の既存グループを維持したまま、その内容を公開済みpropのrig参照へ差し替える。
公開するUSDはグループまでのパスと配置を維持し、配下にはpropの評価済みメッシュを出力する。
Mayaシーンは参照を保持する。UE用シェーダー再現処理は追加しない。

## 操作

1. 背景の作業シーンを開く。公開済みファイルは上書きせず、Asset Workに保存する。
2. Mayaで `|Root|geo_grp|prop_geo_grp|chair_geo_grp` のような、ローカルなグループを1個選択する。
3. Asset Assemblyの **Place Asset** タブで公開済みpropのカードを選ぶ。
   Asset Managerのアセット一覧・メタデータ・サムネイルと共通のカード表示を使用する。
   `bp` 限定ではなく、`main` を含む全propグループが対象。
4. 公開済み候補からVariant、Context、固定Versionを選ぶ。
   例: `prop / main / DeleinChair / default / anim / v005`。手入力は不要。
5. **Replace / Update Selected Group** を実行し、配置を確認する。
   propの原点を選択グループの座標系へ合わせる。自動のサイズ合わせやbbox合わせは行わない。
6. 背景のローカル `cache_geo_set` に対象グループを含めて、作業シーンを保存する。
7. Asset Managerで通常の **Release & Pack** を実行する。

同じグループを選んで別バージョンを指定すると参照を更新する。
新規配置は親の背景グループを選択し、**New Placement in Selected Group** を押す。
親の直下に専用グループを作り、その中へ固定バージョンのpropを参照する。
未公開アセットの作成には **Unpublished asset? Open Extract / Publish** から既存タブへ移動できる。
カードにはMaya形式のAsset Contextが公開済みのアセットだけが表示される。
Extractでmodelを公開した後は、Asset Managerで必要なAsset Contextを公開する。
**Refresh Assets** で公開候補を再取得する。検索入力ではファイルを再走査しない。
一覧の行を選ぶとMayaの対応グループを選択し、現在のprop情報をフォームに表示する。
**Restore Original Geometry** はrig参照を除き、差し替え前の子要素を戻す。
元の子要素は非表示の `__assemblyOriginal` グループ内に退避され、USDには含めない。

## NamespaceとCast

Mayaの参照namespaceは `DeleinChair` のようなアセット名を使用する。
すでに使用されている場合は `DeleinChair_2`、`DeleinChair_3` のように採番し、既存のCastや参照とは統合しない。
Assemblyの固定IDと参照ノードへのmessage接続が識別の正本で、namespaceは表示用。
既存の `assemblyProp_<ID>` を変更するには配置先グループを選択し、**Use Asset Namespace** を押す。
参照ファイルは再ロードせず、固定IDと参照編集を維持する。変更後は作業シーンを保存する。

背景をnamespace付きでショットへ参照すると、propは `DeleinRoomB:DeleinChair` のような入れ子になる。
Shot Builderのnamespace検索はAssembly所有の参照とその子namespaceをCast候補から除外する。
Assembly所有のnamespaceと同名でCastを追加しようとした場合はエラーにし、Cast側の別namespace指定を求める。

## 公開契約

- グループパスとその祖先を維持する。登録後の移動・改名はPublishエラー。
- propごとに自身の `cache_geo_set` が必要。出力メッシュはその集合から取得する。
- メッシュ数、頂点数・頂点順、UV、フェースのマテリアル割り当てはprop側に従う。
- 出力メッシュは配置先グループの直下へ平坦化する。rigのjoint、control、namespaceは階層に持ち込まない。
- 出力名はpropメッシュtransformのnamespaceを除いた名前。同一配置先での名前衝突・複数shapeはエラーにする。
- rigの現在の評価状態を静的に出力する。アニメーションの時間サンプルを出力する機能ではない。
- 原点、回転、非均等スケールを含め、出力のワールド座標を保持する。
- メッシュやフェースへ設定した旧背景のオーバーライドは移植しない。
- 複数の配置先に同じpropを独立参照できる。配置先の入れ子はサポートしない。
- 固定ID、固定バージョン、Resolverで取得した参照パスを背景シーンに保存する。
  対応情報はReleaseの `publish.json` と `build_manifest.json` にも記録する。
- 参照ファイルの欠落・未ロード・外部ツールでの別ファイルへの差し替えを検出してPublishを止める。
- `.mb` は編集用の参照付き背景、USDは静的モデル。Lighting専用の参照なし `.mb` は生成しない。

## 実装と検証

UIは `apps/asset_assembly/place_panel.py`、共通の公開候補取得は `apps/asset_manager/published_catalog.py`、
Maya処理は `dcc/maya/assembly_replacement.py`。独立したGroup Replacementタブは廃止した。
既存 `AssetPublishResolver` と `configured_project_paths` を使用し、パステンプレートは追加しない。
`proxy_usd.export_proxy_usd` は登録された差し替えがある場合だけ専用の静的出力処理へ分岐する。
元のMayaシーンや参照をimportせず、一時的な評価済みメッシュを生成・破棄する。

Maya実機の回帰検証は `tests/maya_assembly_replacement_smoke.py` をmayapyで実行する。
検証用ファイルは `.tmp/assembly-replacement/` のみに生成する。
