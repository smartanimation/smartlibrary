# Shared Shot Context

Shot Managerを起点とする実行時のショット取得APIは
`smartlib.apps.shot_manager.context.get_shot_context(project_config)` とする。
USDを組み立てる既存のShot Context成果物とは別の概念。

- `selected_shot`: Shot Managerの一覧選択。シーケンス選択・選択解除では `None`。
- `scene_shot(service)`: Mayaで現在開いているファイルを既存の
  `ShotManagerService.shot_identity_from_path` で解決する。取得のたびに実シーンを確認する。
- シーン未保存・ショット未特定の場合は `None`。一覧選択・環境変数で補完しない。
- プロジェクトのConfigディレクトリ単位、同一Pythonプロセス内で選択と変更通知を共有する。
- Shot Managerの選択変更時は、別プロセスのDCCが明示的に取得するためのスナップショットも保存する。
  スナップショットからShot Manager自身の選択は復元せず、ライブ変更通知にも使用しない。

Review Layer ManagerはMaya内では常に `scene_shot` を使用する。
Shot Managerで別ショットを選んでも、Maya側の編集対象は変わらない。
Open/Newではドラフトを破棄して再読込し、Save Asで所属ショットが変わった場合も追従する。
同じショットへの通常保存ではドラフトを保持する。

単体起動ではShot Managerの `selected_shot` とその変更通知を使用する。
明示的な `identity` を渡した単体ウィンドウは固定対象として扱う。
単体ウィンドウのローカルなショット選択はShot Managerの共有選択を書き換えない。
UIは `Maya Scene` / `Selected Shot` で取得元を表示する。

Smart AE Browserは起動時に前回のパネル選択を復元する。`Current`を明示的に押した場合だけ、
Shot Managerの最新選択スナップショットへProject / Episode / Sequence / Shotを切り替える。
CurrentはAEPのOpen、Build、Saveを実行しない。
受け渡しは既存のscripts.dcc_context保存APIを利用し、ProjectとConfigディレクトリも記録する。
選択解除を取得した場合はエラーを表示し、古いShotや起動環境変数で補完しない。

パス解決は既存Serviceと共通Path Resolverに委ね、ここではパス階層・テンプレートを追加しない。
