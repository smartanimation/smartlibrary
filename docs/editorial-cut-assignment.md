# Editorial Cut Assignment

## Editorial Unit → Production Sequence

Ingestで登録する受領単位は **Editorial Unit** とする。
シーケンス単位の納品、エピソード全体、PV、Openingなど、１本の受領編集を識別する名前。
例: `ep02/full_edit`、`op/op_edit`、従来と同じ名前の`ep02/s027`。

IngestはProduction Sequence／Shotを登録しない。新規受領のManifestは
`editorial_unit`を記録する。Production SequenceはExport & Intakeのカット割り当てで確定する。
新規画面ではProduction Sequenceを未割り当てで表示し、空欄の出力行があればPublishを停止する。
新規Mappingは`editorial_unit`と行ごとの`production_sequence`を保存する。
Timingのsourceにも両方を記録し、制作ショットから受領元を追跡できる。

保存階層は既存Resolverのまま、`production/editorial/data/<episode>/<unit>`を使う。
Workも`<episode>/<unit>`で保持する。旧受領Manifestのsequenceと旧Mappingのsequenceは
読み取り互換とし、既存フォルダーを移動する必要はない。
ファイル名の新規標準は`{episode}_{editorial_unit}{extension}`。
既存設定の`{sequence}`トークンはIngestに限りEditorial Unitの互換別名として利用できる。

Smart Editorial Exportの`Export & Intake`からカット割り当て画面を開く。

- 編集位置は現在のResolveマーカーの絶対フレーム。XMLを移動済みなら追加オフセットを加えない。
- `Offlineの先頭フレームに対応するタイムラインフレーム`は動画のフレーム0の配置位置。
  動画がtimeline 0から始まりXMLが00:00:05:00からなら、24fpsで最初のOffline Inは120、設定値は0。
- 編集位置とMaya範囲は両端を含む。Maya In変更時はカット尺を保ってOutを更新する。
- 出力欄をクリックして対象を選ぶ。複数行を選択し、作業ショット名を一括指定できる。
- シーケンス列を編集して制作上の場面ごとに割り当てる。複数行を選択して一括変更も可能。
  同名ショットもシーケンスが異なれば別の作業ショットとして扱う。
- 上部のEditorial UnitはWorkと元動画の識別に使用する。出力は一覧のProduction Sequenceごとに
  Editorial Publish、Sequence音声、Shot Dataを作成する。編集位置は元動画内の位置を維持する。
  シーケンス単位、エピソード一括、PV／Openingを同じ画面で扱える。
  旧保存データにシーケンス欄がない場合は元の受領シーケンスを引き継ぐ。
- 同じ作業ショットのMaya範囲が重なる場合は明示的に修正する。連続配置ボタンも使用可能。
- 確認済みチェックを入れて実行する。全体尺の違いは許容するが、選択範囲が動画外に出る場合やfpsが異なる場合は停止する。

割り当てはWorkのmanifest.jsonへ保存し、Publishのmetadata/editorial.jsonにも記録する。
マーカー情報が変わった場合は古い割り当てを再利用しない。確認済みチェックは毎回やり直す。
キャンセル時およびPreflight only時はProduction Publishを行わない（Workの保存は行う）。

## 出力

既存の共通Resolverのshot_data_version_dirを使用する。

- `editorial_reference/cuts/<編集カット>/<version>/offline.mov`: 編集カットごとのOffline。
- `editorial_reference/main/<version>/offline.mov`: 作業ショットのMaya範囲へ配置した参考動画。
- `audio/<version>/<作業ショット>.wav`: 上記参考動画と同期した音声。
- `editorial_timing/main/<version>/editorial_timing.json`: 明示Maya範囲と編集区間の対応。

Maya範囲間の空きは黒映像・無音で埋める。Offlineに焼き込まれた編集を等速で配置するため、
各カットのMaya範囲は編集尺と同じ長さとする。元ソースの速度変更の再構成は行わない。
Storyreelも作業ショットの参考動画からMayaフレーム番号で生成する。
ハンドルはMayaの作業範囲を拡張するが、カット参考動画・音声には含まない。

Sequenceのカットごとの情報と作業ショットの対応は保存する。
本機能はMaya Work Stageの作業ショット入力とOffline出力を対象とし、
完成レンダーの編集への自動差し戻しは別機能。

## Stageの代替読み込み

Stage AAF / XML / EDLはResolveのタイムライン読み込みを先に試す。
失敗した場合はIngest済みOffline動画でタイムラインを作り、参照ファイルから範囲マーカーを生成する。
XMLはxmemlの映像clipitem、EDLはnon-drop-frameの映像イベントが対象。
XMLの位置はシーケンス先頭からの相対位置、EDLは最初の映像イベントのRecord Inを起点にする。
音声イベントを別ショットとして数えない。エフェクトや元ソースの再構成は行わない。

代替処理の使用と元の読み込みエラーはWork manifestに記録する。
Stage後はOfflineと編集点の位置合わせを確認し、Export & Intakeで割り当てを確定する。
XMLのfps不一致・未解決のトランジション位置、drop-frame EDL、マーカーの部分的な作成失敗はエラーにする。
