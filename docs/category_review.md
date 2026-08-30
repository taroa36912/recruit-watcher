# カテゴリ要確認リスト

以下の target は分類に自信がなく、手動での確認が必要です。各行に `id / company / URL / 理由` を記載します。

## asset_management（資産運用）

- `nomura-am-asset_management-72-nomura-am` / 野村AM / https://www.nomura-recruit.jp/graduate/ / カタログの「野村AM(クオンツ)」は official_url が null のため、野村グループ新卒採用サイトを流用。役割は「クオンツ」であり資産運用職かは未確定。URLの妥当性と役割の確認が必要。

## trader（トレーダー）

- `jp-35-it-71-qdt` / みずほフィナンシャルグループ / https://www.mizuho-fg.co.jp/saiyou/recruit/ / カタログの「みずほ(QDT)」の QDT が何を指すか確信できず（クオンツ・デスク・トレーディングの可能性）。trader に暫定割当したが quant の可能性もある。

## research（研究職）

- `jp-32-it-71-r-d` / サントリー / https://www.suntory.co.jp/recruit/fresh/ / 「R&D/食品開発」で、食品開発寄りであり研究職（R&D）に含めるか微妙。
- `ibm-consulting-73-ibm` / IBM / https://www.ibm.com/careers/jp-ja/ / カタログでは consulting に分類されていたが「基礎研」のため research に変更。IBM基礎研究所は研究職だが元分類との乖離を確認したい。

## data_science（データサイエンス）

- `jp-24-it-72-ds` / メルカリ / https://careers.mercari.com/jp/ / 「DS/エンジニア」の混在。data_science に暫定割当したが、エンジニア寄りなら it のままが適切。

## 割当を見送ったもの（要検討）

以下はデータサイエンス・研究・資産運用・トレーダーの候補になり得るが、自信がなく現行カテゴリ（it 等）のまま据え置いたものです。

- `dena-it-72-dena-ai` / DeNA / https://student.dena.com/ / 「DeNA(AI)」。AI 職は data_science 候補だが、AIエンジニアとデータサイエンティストの区別が付かず it のまま。
- `ntt-it-68-ntt-docomo-ai` / NTTドコモ / https://information.nttdocomo-fresh.jp/ / 「NTT docomo(AI)」。同上の理由で data_science にせず it のまま。

## カタログに該当するが URL が無く target 未登録のもの

`company_catalog.yaml` に記載はあるが `official_url` が null のため target を立てられなかった候補です。URL を用意できれば daily プロファイルに追加できます。

- 味の素(DS) / 味の素(DS)
- AGC(DS) / AGC(DS)
- 日立(DS) / 日立(DS)
- ADK(DS) / ADK(DS)
- SBI(DS) / SBIホールディングス(DS)
- みずほ第一FT / みずほ第一FT（trader 候補）
- 野村AM(クオンツ) / 野村AM（asset_management 候補、上記で流用 URL を仮登録済み）
