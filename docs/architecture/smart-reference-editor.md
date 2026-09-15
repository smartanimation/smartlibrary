# Smart Reference Editor

Mayaの作業シーンとCastingを比較し、選択した参照だけを追加・置換するツール。

## 起動

SmartMenuの **Reload SmartMenu** を実行し、**File → Smart Reference Editor** を選択する。
メニューコードが古いままの場合はMayaのPythonタブから次を実行する。

```python
from smartlib.apps.smart_reference_editor import show
show()
```

設定は既存の `PROJECT_CONFIG_DIR` を使用する。明示する場合は `show(config_dir=...)`。

## Casting Sync

1. Scope、Episode / Sequence、Shotを選択する。ShotはDepartmentも選択する。
2. **Refresh Comparison** で最新のCastingと現在のシーンを比較する。
   初期表示はCasting登録アセットの全件（一致済みを含む）。Asset / Variant / Namespaceを表示する。
   **Changes only** で差分のみに絞れる。カメラなどCasting外の参照は通常非表示で、
   **Show references outside Casting** を有効にすると確認できる。
3. 新規追加分は既定で選択される。**Select added only** で追加分だけに戻せる。
4. **Changed** を選択すると、そのNamespaceの既存参照をCastingの解決先へ置換する。
5. **Preview Changes** で変更前後のパスを確認し、**Apply Selected** で適用する。

同じファイルを複数回使うアセットも、CastingのNamespaceごとに比較する。
`In sync`、`Not in casting`、`Nested` は変更しない。`Unresolved` とNamespaceの
`Conflict` は適用対象にできない。Castingから削除された参照を自動削除しない。
比較は手動更新。ShotではShot Casting、SequenceではSequence Castingを読み、
SequenceからShotへのCasting同期・公開は既存のSmart Castingが担当する。

## Production Relink

1. クライアントから受け取ったMayaシーンを開く。
2. **Production Relink → Scan References** を実行する。
3. 各参照の **Production asset** から置換先を選ぶ。
4. 置換する行にチェックし、プレビューを確認して適用する。

候補はプロジェクトの既存 `ProjectPaths.asset_variant_root` と
`AssetPublishResolver.resolve` で解決したMaya公開ファイルのみ。
Shotは選択Department、Sequenceは既存のlayoutルールを使用する。
同じパス、または一意の同名ファイルのみ候補を提案する。提案行も自動適用しない。
ファイル名が異なる場合は明示的に対応付ける。テクスチャなどのfileノードは対象外。

## 適用と失敗時の動作

- 適用前にシーン名・参照ノード・パス・Namespace・読み込み状態を再照合する。
- Casting Syncは適用前にCastingの解決先も再確認する。
- 全選択行のファイル存在とNamespace衝突を、最初の変更前に検証する。
- 置換は同じreference nodeを再利用し、Namespaceとreference editsを保持する。
- 読み込み解除済み参照はロードせずパスを置換する。その参照のedit互換性はロード時に確認する。
- ロード済み参照は置換後にfailed editsを確認する。失敗時は後続処理を止め、結果を表示する。
- 失敗した操作も一部変更済みの場合がある。自動ロールバックや自動保存は行わない。
  事前に作業シーンのコピーを保存し、エラー時はシーンを確認する。
- ネストした子参照は表示のみ。親参照のアセットを置換する。

## 構成と検証

- `apps/smart_reference_editor/service.py`: 既存Casting/Resolverの利用、差分判定、候補生成。
- `apps/smart_reference_editor/ui.py`: PySide6/PySide2の2タブUI。
- `dcc/maya/reference_editor.py`: Mayaの参照スキャン、適用前検証、追加・置換。
- `tests/test_smart_reference_editor.py`、`tests/test_smart_reference_editor_ui.py`: 差分・適用・UI検証。
- `tests/maya_reference_editor_smoke.py`: mayapyで実行する参照処理の検証。
  Maya 2024/2026で、複数インスタンス追加、キー・参照編集保持、unloaded参照の置換、
  ネスト参照の追加と子参照の置換拒否を検証。生成物は `.tmp/` に保存する。

Maya fileコマンドの仕様: [Autodesk command reference](https://help.autodesk.com/cloudhelp/2026/ENU/Maya-Tech-Docs/CommandsPython/file.html)。
