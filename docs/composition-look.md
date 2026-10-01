# Smart Composition: late Preview Look assignment

Animationの各キャラクター行にLook (Off / version)とLatest Lookを表示する。
Rigの固定参照から対象Asset/Variantの正式Look Publishを探索し、保存先をResolverで検証する。
既存CompositionにLook指定がなければ初期値はOff。自動でLatestへ切り替えない。

1. 既存Compositionを開く。
2. キャラクター行のLookからSubset / versionを選択する。Offで適用を解除する。
3. プレビューで確認し、Save New Compositionで固定する。

Refresh Versionsは公開候補とLatestを更新し、現在の選択を保持する。Resetは保存済み選択へ戻す。
Lookを変更した状態での動画出力には、先にCompositionの保存が必要。

プレビューはメモリ内のみ。保存時は必要なAnimation Productを新規作成し、元deformを固定参照する。
deformの再出力や既存Compositionの上書きは行わない。Look解除も新しいProductとして保存する。
Look・参照Textureのハッシュがプレビュー後に変化していた場合は保存を拒否する。

現在の対応は全メッシュ割り当て。メッシュ名から対応候補を作り、トポロジー・UVの指紋を検証する。
欠損・曖昧な名前・UV不一致では停止し、直前の表示が残る場合はエラー欄で明示する。
現行の指紋は追加UVセットも含むため、未使用UVセットの相違も不一致として扱う。
