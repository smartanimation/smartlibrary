# Maya Preview Geometry / Look Publish

Asset ManagerでAsset・Variantを選び、Publishタブを開く。

- Publishは「種別／公開履歴／現在シーンの設定」の3ペインで表示する。
- Geometry → Publish Geometry USD: 現在Mayaで開いているシーンからgeo.usdを公開する。
- Look → Publish Preview Look: 右ペインでGeometry版・Texture構成版を選択してlook.usdとpreview.usdaを公開する。

Asset ManagerをMaya内で使用する。ファイル選択ダイアログや別のPublishウィンドウは開かず、
現在シーンの未保存編集も含める。対象Asset/VariantのMaya Work Sceneとして一度保存して命名しておく。
model / look / rig等のdeptによるGeometry・Look Publish制限は設けない。
Textureもdeptで制限せず、従来どおりDCCなしでDataからPublishできる。
元シーンの保存・再読込は行わない。Manifestのscene_stateに現在シーン／保存済みシーンの区別、
未保存変更フラグ、出力フレームを記録する。source_sceneはディスク上の元ファイルの固定参照。

依存先のLatestは各種別・Subsetのlatest.jsonを参照する。GeometryとLookのバージョン番号は独立。
初期選択はLatestとし、Refreshでは明示的な選択版を保持する。

PreviewタブのCheck current scene in usdviewで、Geometryのみ／Geometry＋Lookを一時出力して
既存usdviewを起動する。Lookの依存先はPublishペインで選択する。一時チェックはPublishを採番しない。
チェック後も編集でき、正式Publish時には現在シーンから再出力する。編集した場合は再チェックする。

Geometryの対象はcache_geo_set内のメッシュ（入れ子セット・グループを含む）。
namespaceを除いたTransform名が一意である必要がある。リグ、スキニング、マテリアルは含めず、
現在の保存フレームの静的Geometryを出力する。保存先は既存Resolverのmodel/subset/version。

Lookは、Geometry版とシーンのメッシュパス・接続・UVの一致を検証する。
初期対応は全Meshへの単一Lambert割り当て。colorへのfile.outColor直接接続、または画像未接続の単色に対応する。
単色ではMayaから取得したlambert.colorのRGB値をUsdPreviewSurface.diffuseColorへ直接設定する。
単色だけの場合、Texture compositionでNoneを選択できる。画像と単色の混在も可能で、画像側には正式Textureが必要。
正式Texture構成から同名かつSHA-256一致の画像を使用する。未公開・改変画像は拒否する。
Raw/sRGB、place2dTextureのrepeatUV・offset、wrapU/V、Lambert diffuseをPreviewへ反映する。
面単位割り当て、透明、バンプ、複雑なノードなど未対応設定はエラーにする。
Lambert.colorへ直接接続したfileノードは、静止画像とUV Tiling Mode = UDIM (Mari)に対応する。
UDIMの全タイルをTexture Publishへ登録し、Mayaの画像と名前・内容を一致させる。
Look Publishでは選択したTexture構成のタイルを同じ出力先へ固定するため、
元のタイルが複数のTextureバージョンに分かれていても使用できる。
アニメーション画像・UDIM以外のタイリング方式は未対応。
セル特有の陰影や輪郭線シェーダー、Arnoldなど最終レンダラーの再現は対象外。

look.usdはUsdPreviewSurfaceのMaterialとBindingのみを保持し、preview.usdaから
選択したGeometryと合成する。ManifestはGeometry・Texture構成・元シーンを固定参照する。
