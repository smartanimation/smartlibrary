# Asset Manager Texture Publish

Asset ManagerでAssetとVariantを選択し、Data → Textureを開く。
Publish → Texture → Texture Dataからも移動できる。
MayaやHoudiniの起動、Work Sceneの選択は不要。

1. Explorerから画像またはフォルダーを一覧へドロップする。Import texturesからも選択できる。
2. 取り込み画面でSubset、ファイル一覧、UsageとColor spaceを確認し、Import allを押す。
3. Data一覧で公開したいファイルの行を選択し、Publish selectedを押す。複数行選択可能。
4. Publish → Textureで現在の全構成、各画像の公開元バージョン、Publish historyを確認する。

取り込み画面では1行のUsage・Color spaceを設定してCopy settings（Ctrl+C）でコピーできる。
Ctrl／Shiftで貼り付け先の行を複数選択し、Paste settings（Ctrl+V）で一括適用する。
ファイル名・画像・UDIMは変更しない。

Dataは既存Resolverのasset_data_version_dirで取り込みバッチを保存し、textures.jsonに
全取り込み済みファイルの参照を保持する。同名ファイルの再取り込みは新しいData版へ保存する。
元ファイルを移動・削除しても取り込み済みの画像を使用できる。Data一覧はNew / Modified /
Publishedを表示する。以前の公開画像だけが存在する場合はNot importedとして表示する。

フォルダーは再帰的に読み取る。PNG、JPEG、TIFF、EXR、HDR、TGA、BMP、TX、TEX、DDSを対象とし、
フォルダー内の対象外ファイルは除外して画面に表示する。PSDなど編集用のプロジェクトは対象外。
サムネイルはQtが読み込める形式のみ表示する。全形式の画像デコード検証は行わず、
拡張子・非空・ファイル名衝突・コピー前後のSHA-256を検証する。

Publishは選択した画像だけを新規バージョンへコピーし、未選択の公開画像は旧版の参照を引き継ぐ。
例: v001でbody/faceを公開し、v002でbodyのみ更新した場合、v002の構成はv002/bodyとv001/face。
Publishから外すときはData一覧で選択してRemove selected from Publishを使用する。
この操作も新規Publish版を作成し、画像の実体・Data・過去のManifestは削除しない。
保存先は既存ProjectPaths Resolverから取得する。ファイル名とバイト列は変更せず、
フォルダー階層は引き継がない。同名画像が複数存在する場合は大文字小文字を区別せず拒否する。
既存バージョンと元画像は上書きしない。コピー失敗時はlatestを更新せず、未完了バージョンは一覧に出さない。

publish.jsonのartifactsに引き継ぎを含む全構成のパスとハッシュを記録する。
filesにはその版でコピーした画像、source_filesには今回の入力のパスとハッシュを記録する。
originsは各画像の公開元バージョン、changed / removedは変更点、previousは前版Manifestの固定参照。
既存Manifestにこれらの項目がなくても、その版のartifactsを完全な構成として読み込める。
texturesにはartifactsと同じキーでusage / color_space / udimを記録する。
`base.1001.exr`などの末尾のUDIM番号は記録するがファイルを改名しない。
`colNml_u21_v1.png`などの用途・タイルをファイル名から自動解釈しない。
UsageとColor spaceは記録のみで、画像変換やMaterialへの自動割り当ては行わない。
Look側が使用するTexture Publishを選び、参照を固定する。

検証: `tests/test_texture_publish.py`（DCCなしでQt DropからPublish、衝突拒否、コピー失敗、旧版保持）。
