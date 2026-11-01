# 仕様ソースとライセンス／規約判断

> このファイルは `configs/sources.yaml` から `python -m src.cli sources` で生成される。直接編集しない。
> 取り込みは `status = allowed` かつ `evidence_role = first_party_spec` のソースのみ（`src/sources.py` が強制）。
> 規約・ライセンスが不明確なソースは取り込まず `blocked` として残す。レビュー・Q&A は証拠に使わない。

| ID | ソース | 種別 | 証拠ロール | 取得方法 | ライセンス／規約 | 再配布 | 状態 | 判断 |
|---|---|---|---|---|---|---|---|---|
| `S0-synthetic-seed` | SpecSure 合成 seed（架空商品・手作業で属性化） | synthetic_manual | first_party_spec | 手作業で作成（data/seed/specbench_v0.yaml）。ネットワーク取得なし | プロジェクト作成物（公開時は CC0-1.0 を想定、要最終判断）。架空のブランド・型番・機種名のみ。GTIN は GS1 店内用プレフィックス 20–29 で実在商品と非衝突 | yes | **allowed** | claude (Phase 1 実装時、要 Yuta 確認) (2026-10-31) |
| `S1-test-fixtures` | テスト用極小 fixture | synthetic_manual | first_party_spec | 手作業で作成（fixtures/） | プロジェクト作成物。テスト専用。ベンチには含めない | yes | **allowed** | claude (Phase 1 実装時、要 Yuta 確認) (2026-10-31) |
| `C1-manufacturer-spec-pages` | メーカー公式の仕様ページ・仕様表・取扱説明書 PDF（個別サイト） | manufacturer_official | first_party_spec | 未実施（手作業での属性化を想定。大規模スクレイピングはしない） | サイトごとに異なる（未確認）。多くのサイトで転載・二次利用の条件が個別に定められており、サイト単位の規約確認が必要。事実データ（数値・互換機種）の属性化と本文の非保存を前提に、メーカーごとに判断する（Phase 2 で判断したメーカーは C1-<maker> として個別に記載） | unknown | **blocked** | claude (Phase 1、メーカー単位のレビュー待ち) (2026-10-31) |
| `C2-retailer-official-api` | 公式 API・商品フィードを持つ小売（例：大手 EC の商品 API） | retailer_official_api | first_party_spec | 未実施（API キーと利用規約の確認が前提） | 各 API の利用規約による（未確認）。商品 API は表示目的・再配布禁止・キャッシュ期限などの条件を課すことが一般的と想定。ベンチとしての再配布可否は規約確認まで不明 | unknown | **blocked** | claude (Phase 1、規約レビュー待ち) (2026-10-31) |
| `C3-gs1-registry` | GS1 の GTIN 登録情報（Verified by GS1 / GEPIR 等） | identifier_registry | identity_only | 未実施 | GS1 の利用規約による（未確認）。照会回数・自動取得・再配布に制限がある想定。同一性解決の補助に使う場合も規約確認が必要 | unknown | **blocked** | claude (Phase 1、規約レビュー待ち) (2026-10-31) |
| `C4-open-food-facts` | Open Food Facts / Open Beauty Facts | open_database | none | 未実施 | データベースは ODbL、個別コンテンツは DbCL、画像は CC BY-SA（とされる。採用前に再確認）。再配布は条件付きで可能と考えられるが、クラウドソースでありメーカー第一者の仕様ではないため、証拠ソースには使わない。成分辞書の拡充など補助用途なら検討余地あり | conditional (share-alike) | **reference_only** | claude (Phase 1) (2026-10-31) |
| `X1-customer-reviews-qa` | カスタマーレビュー・Q&A・掲示板 | user_generated | none | 取得しない | -。設計上の明示ルールとして証拠ソースに使わない（sources.py がこの kind を拒否する） | no | **prohibited** | 設計ルール (2026-10-31) |

## メーカー公式仕様ページ（C1）の規約判断 — Phase 2 real_pilot

> 判断は Claude Code による**暫定**（法務確認前）。`allowed_internal` = 事実情報（型番・数値・対応機種名・原材料名）の
> 少量の手作業属性化と非公開リポジトリ内での研究利用のみ可、本文・画像は保存しない、**再配布不可**（公開ベンチに入れない）。
> 規約が「著作物」ではなく「情報・データ」一般の利用を私的使用に限定するもの、データ抽出・営利目的の利用を明示的に禁じるものは `blocked`。

| ID | メーカー | 規約 URL | 確認日 | 状態 | 判断根拠 |
|---|---|---|---|---|---|
| `C1-calbee-jp` | カルビー（calbee.co.jp 商品情報ページ） | https://www.calbee.co.jp/policy/ | 2026-10-31 | **allowed_internal** | 対象は知的財産権のあるもの（法令上の禁止の確認）。事実情報の属性化・データ収集の禁止条項なし。ページに JAN の記載なし |
| `C1-canon-jp` | キヤノン（canon.jp / personal.canon.jp 消耗品ページ） | https://canon.jp/term-of-use | 2026-10-31 | **allowed_internal** | 対象は著作物としてのコンテンツ。事実情報の属性化・データ収集の禁止条項なし。personal.canon.jp も同社運営として同条件と仮定（要確認） |
| `C1-epson-jp` | エプソン（epson.jp 消耗品情報ページ） | https://www.epson.jp/tou/ | 2026-10-31 | **allowed_internal** | 規約の対象は「本コンテンツ（著作物）」で、法令で許可された範囲を除外。型番・対応機種名などの事実情報の属性化には言及なし。データ収集の禁止条項なし |
| `C1-glico-jp` | 江崎グリコ（glico.com 商品ページ） | https://www.glico.com/jp/termsofuse/ | 2026-10-31 | **allowed_internal** | 対象は著作物。事実情報の属性化の禁止なし。禁止行為の「不特定多数を対象とする営業目的の行為」は将来の商用化時に再確認が必要 |
| `C1-panasonic-jp` | パナソニック（panasonic.jp 商品仕様ページ） | https://www.panasonic.com/jp/about/terms-of-use.html | 2026-10-31 | **allowed_internal** | 対象は著作物。事実情報の属性化・データ抽出の禁止条項なし（禁止行為は誹謗・不利益等の一般条項） |
| `C1-anker-jp` | アンカー・ジャパン（ankerjapan.com） | https://www.ankerjapan.com/pages/terms | 2026-10-31 | **blocked** | 手作業でもベンチ構築は「体系的なデータ収集・データ抽出」に当たりうる → blocked。パナソニックに差し替え |
| `C1-brother-jp` | ブラザー（brother.co.jp） | https://www.brother.co.jp/copyright/index.aspx | 2026-10-31 | **blocked** | データそのものを明示的に対象としている → blocked |
| `C1-elecom-jp` | エレコム（elecom.co.jp） | https://www.elecom.co.jp/legal/ | 2026-10-31 | **blocked** | 「情報」を対象にし、営利の準備行為も禁止（将来の ClaimWatch 等が該当しうる）→ blocked |
| `C1-kao-jp` | 花王（kao.com/jp） | https://www.kao.com/jp/terms-of-use/ | 2026-10-31 | **blocked** | いかなる方法・目的の使用も許諾制 → blocked（化粧品の全成分データ源として有望だが要個別許諾） |
| `C1-meiji-jp` | 明治（meiji.co.jp） | https://www.meiji.co.jp/regulation/ | 2026-10-31 | **blocked** | 「情報」一般の利用を私的使用に限る文言で、事実情報の属性化も対象に含まれうる → 判断が付かず blocked。カルビーに差し替え |
| `C1-nissin-jp` | 日清食品グループ（nissin.com） | https://www.nissin.com/jp/docs/terms/ | 2026-10-31 | **blocked** | 「情報等」一般が対象 → blocked。グリコに差し替え |

## Phase 1 で実際に取り込んだソース

- Phase 1: `S0-synthetic-seed`（SpecBench v0 seed、すべて架空商品）と `S1-test-fixtures`（テスト専用）のみ。
- Phase 2: real_pilot（30 商品）として `allowed_internal` のメーカー 5 社（Epson / Canon / Panasonic / Calbee / Glico）の
  公式仕様ページから手作業で属性化（`data/real_pilot/seed_real_pilot.yaml`、合成 seed とは別マニフェスト）。
- 未審査のメーカー・小売（`C1-manufacturer-spec-pages` の総称、`C2`・`C3`）は引き続き取り込まない。

## 状態の定義

- **allowed**: ライセンス／規約の判断済みで取り込み可
- **allowed_internal**: 内部研究用に取り込み可（暫定）。再配布不可・公開ベンチには含めない
- **blocked**: 規約が不明確、または再配布不可の可能性が高いため取り込まない
- **reference_only**: 参照は可だが第一者仕様ではないため証拠ソースには使わない
- **prohibited**: 設計ルールとして証拠に使わない（レビュー・Q&A）
