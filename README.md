# Youtube-Shorts-Auto

AIショート動画の生成パイプライン。企画 → 生成 → 後処理 → 人間レビュー → 投稿 → 公開 → 計測 →
実績を企画へフィードバック、までを1本の CLI で回します。

## これは何のためのものか

参照した [AI生成ショート](https://www.youtube.com/shorts/kHwpZcPRYpE) と同型の動画を作りますが、
**広告収益を直接狙うツールではありません。**

事前調査の結論はこうです。

- YouTube Shorts の RPM は **$0.03〜0.10 / 1,000再生**
- 収益化には **登録者1,000人 + 直近90日で1,000万ショート再生**。そこまで収益はゼロ
- 到達しても月1,000万再生で **$300〜1,000（4.5〜15万円）** が上限
- YouTube は2025年7月に「inauthentic content（テンプレート反復の大量生産）」を収益化対象外と明記。
  2026年初頭にAIショート量産チャンネルの収益化停止・削除が続出している

したがって広告収益単体では割に合いません。このリポジトリが実装しているのは
**方針C — 月1万円以内・1日2〜3本の小規模で回し、「どのジャンルが実際に再生を取るか」という
実データを買うための実験装置**です。当たりが見えたら拡大、見えなければ数万円で止める。
その判断を勘ではなく `shorts-auto report` が出す数字で下せるようにすることが第一目的です。

上限と中止判断の基準はコードに埋め込んであります（`budget.py` / `report.py`）。

## セットアップ

```bash
sudo apt-get install -y ffmpeg fonts-noto-cjk   # 後処理とCJKタイトル焼き込みに必須
pip install -e ".[dev,review]"
cp .env.example .env                            # GEMINI_API_KEY と ANTHROPIC_API_KEY を記入
shorts-auto init
shorts-auto doctor                              # 何が足りないか一覧で出る
```

`shorts-auto doctor` は、ツール・APIキー・OAuthトークン・設定の整合・今月の支出・価格表の鮮度・
`sync-stats` の放置を一度に診断します。**キーを入れる前でも動く**ので、まずこれを実行してください。

### YouTube の OAuth

Codespace は非対話なのでブラウザが必要な認証は通せません。**ローカルマシンで**取得して持ち込みます。

1. Google Cloud Console → APIs & Services → Credentials で
   **OAuth client ID（種別: Desktop app）** を作成し JSON をダウンロード
2. `config/oauth/client_secret.json` に置く
3. ブラウザのあるマシンで `shorts-auto auth --channel ja` / `--channel en` を実行
4. 生成された `config/oauth/token_ja.json` / `token_en.json` をこの環境にコピー

`config/oauth/` は `.gitignore` 済みです。

## 使い方

```bash
shorts-auto status                 # いま何がどの段階にいて、次に何をすべきか
shorts-auto ideate --count 3       # 企画を立てる（実績に応じて腕を抽選）
shorts-auto generate --limit 3     # Veo で動画生成（ここで課金が発生する）
shorts-auto postprocess            # 1080x1920化・音量正規化・タイトル焼き込み・サムネ抽出
streamlit run review_app.py        # ← 人間の承認ゲート。ここを通らないと投稿されない
shorts-auto publish --limit 3      # private でアップロード
shorts-auto go-live                # ← public に切り替える。これをやるまで再生されない
shorts-auto sync-stats             # 再生数・視聴維持率を取り込む（毎日実行）
shorts-auto report                 # 拡大か中止かを判断するための数字
```

課金せずに動作確認するには:

```bash
shorts-auto generate --dry-run     # fake バックエンド。実価格は表示するが課金しない
shorts-auto publish --dry-run
shorts-auto go-live --dry-run
shorts-auto gc --dry-run           # DBが参照していない生成物を掃除
```

`--dry-run` は `--backend` より優先されます。安全のためのフラグが別のフラグで打ち消されないためです。

## 設計上の要点

### 支出の上限はコードで強制する

`config/settings.yaml` の `budget.monthly_usd` / `budget.per_run_usd` を超える生成は
`BudgetExceeded` で拒否されます。これを実際に成立させているのは3つの仕組みです。

- **課金台帳は追記専用**。`generations` と `llm_calls` は既に出ていったお金の記録で、
  どのUI操作でも削除できません。レビューUIの「再生成」は台帳に**行を足す**操作です
- **外部副作用ごとに即コミット**。生成やアップロードが確定した直後に `conn.commit()` するので、
  途中でクラッシュしても「課金済みなのに記録がない」状態になりません
- **同時実行を排他**。`work/.generate.lock` により、2つの実行が互いの支出を見落として
  それぞれ上限まで使う事故を防ぎます

`generate --prompt` の単発実行も同じ台帳に計上されるので、スモークテストの繰り返しで
上限をすり抜けることはできません。

### private のままでは何も測れない

`publish` は必ず `private` でアップロードします。YouTube Studio で目視確認したあと、
**`shorts-auto go-live` で public に切り替えて初めて再生が始まります。**
計測の起点は投稿時刻ではなく公開時刻（`went_public_at`）です。private だった時間を
露出時間に数えると、あらゆるレートを過小評価するためです。

`report` と `status` は private のまま滞留している本数を警告します。

### 8つの腕 = A/Bテストの単位

腕は **(シリーズ, 言語)** の組です。`config/series/*.yaml` が1ファイル1ジャンルで、
**ジャンル追加はファイルを1つ足すだけ**です。

| ID | 内容 | 言語 |
|---|---|---|
| `micro_camera_doc` | マイクロカメラ風の動物ドキュメンタリー | ja + en |
| `miniature_rescue` | ミニチュア救助（Tiny World Rescue） | ja + en |
| `ai_asmr` | サバイバル/不可能物体系ASMR（飽和したガラス果物系は除外） | ja + en |
| `survival_moment` | 難所を越える一瞬（参照動画と同型） | ja |
| `retro_japan` | 昭和・大正のタイムスリップ映像 | ja |

**1企画 = 1動画 = 1チャンネル**です。同じ映像を2チャンネルに上げると再利用コンテンツ判定を受け、
[違反はチャンネル全体の収益化停止につながります](https://vidiq.com/blog/post/youtube-reused-content-policy-guide/)。
片方の違反が両方を巻き込む構造は、ダウンサイドを限定するという方針Cの主旨に反します。

配分は再生数に比例（`scoring.py`）。ただし各腕が10本たまるまでは均等配分のままで、
判定も出しません — 3本程度の実績で重み付けしても、それは学習ではなくノイズの増幅だからです。

スコアは**再生数に線形**です。Shorts の収益は再生数に線形で、分布はロングテール（大半が数百〜数千、
稀に数十万）。つまり価値があるのは「たまに大当たりを出す」腕であって「中央値が高い」腕ではありません。
対数スコアだと、結果を決める信号をちょうど潰してしまいます。

抽選は**累計実績を見て、目標シェアから最も遅れている腕を選ぶ**方式です。実行ごとに枠を作り直す
実装だと、1日3本では上位3腕しか選ばれず、探索用の最低配分が実質ゼロになります。

### 人間レビューは品質管理であり、同時にコンプライアンス

YouTube の2025年ポリシーは「人間の編集介入の証明」を審査の鍵にしています。`reviews` テーブルが
その監査証跡です。承認には3項目のチェックが必要で、これは prompt では確実に防げない失敗
（文字の焼き込み・実在人物の顔・禁止事項）に対応します。却下理由は次回の `ideate` に
負例として渡されます。

### 重複排除は2層

1. `dedup_key`（正規化ハッシュ + UNIQUE制約）— 再実行や二重実行で同じ企画が入らない
2. Jaccard類似度 — 過去の企画を言い換えただけの案を弾く。閾値は **0.70**（YouTube の
   inauthentic content 判定でよく挙げられる「台本類似度70%未満」に合わせている）

### AI生成であることの開示

- 投稿時に `status.containsSyntheticMedia = True` を必ず立てます（YouTube の Altered/Synthetic
  開示フィールド。2024年10月追加）。パラメータ化されていないので設定で外せません
- 説明欄の**先頭**に開示文を入れます。YouTube はほとんどの画面で説明を省略表示するため、
  末尾の開示は読まれない開示です
- `go-live` のときに YouTube 側に開示が記録されたか**読み戻して確認**します。
  設定して送っただけでは、記録された証明にはなりません

これは2026年8月に施行された EU AI Act のラベリング義務にも対応します。

### Veo のプロンプト

[Google 公式のガイド](https://cloud.google.com/blog/products/ai-machine-learning/ultimate-prompting-guide-for-veo-3-1)
が推奨する5要素構成で組み立てます。

```
[カメラワーク] + [被写体] + [動作] + [場所・状況] + [スタイル] + [音声]
```

公式が「最も効果が大きい」とするカメラワークを明示的に制御するため、企画LLMには1つの文章ではなく
個別フィールドを返させています。

**否定形はポジティブプロンプトに書きません。** 公式が「曖昧な否定は逆効果」と警告しているためで、
避けたいものはすべて `negative_prompt` に入れます。シリーズごとの英語の `negative_prompt` と
`settings.yaml` のグローバル指定がマージされます（`banned` は日本語で企画LLM向けの指示なので、
Veo には届きません）。

音声も必ず指示します。Veo 3.1 の強みは映像と音声の同時生成で、このジャンルは音が価値の半分以上です。
ただし**セリフ・ナレーションは negative_prompt で抑制**します。英語のセリフが入った動画は
日本語チャンネルに流せなくなるためです。

### 投稿ペース

YouTube Data API の既定クォータは 10,000 units/日、`videos.insert` が 1,600 units なので
**物理上限は6本/日**。`settings.yaml` はさらに低い3本/日に絞っています。安定した投稿ペースが、
YouTube から見てチャンネルとコンテンツファームを分ける要素だからです。

`ideas_per_day` が `publish_per_day` を超えていると `doctor` が警告します。超えた分は
生成コストを払ったまま投稿されずに滞留するだけです。

## テスト

```bash
pytest
```

97件。APIはモックせず呼びません。検証しているのは、再現した欠陥それぞれに対する回帰
（再生成で台帳が消えないこと、クラッシュしても課金記録が残ること、同じ企画が二度投稿されないこと、
低シェアの腕も必ず抽選されること、計測欠測が満点扱いにならないこと、`%{...}` を含むタイトルが
改変されないこと、日本語の説明文がバイト長で切られること）です。

## 構成

```
config/
  settings.yaml          予算・投稿ペース・モデル・判定閾値
  channels.yaml          ja/en チャンネルとOAuthトークンの場所
  series/*.yaml          ジャンル定義（1ファイル1ジャンル）
src/shorts_auto/
  budget.py              月次/実行あたりの上限と排他ロック
  pricing.py             モデル別の秒単価とリクエスト検証
  dedup.py               重複排除の2層
  scoring.py             実績→腕への配分と抽選
  report.py              拡大か中止かの判断材料
  doctor.py              セットアップ診断
  ffmpeg.py              1080x1920化・音量正規化・タイトル焼き込み
  youtube.py             Data API / Analytics API
  backends/              VideoBackend プロトコルと Veo 実装（差し替え可能）
  stages/                ideate / generate / postprocess / publish / golive / analytics
review_app.py            Streamlit の承認ゲート
```

データモデルは**課金台帳と成果物を分離**しています。`generations`（追記専用・削除不可）と
`renders`（差し替え可能）を分けることで、成果物を作り直しても支出の記録が消えません。
`posts` は `UNIQUE(idea_id)` で二重投稿を構造的に防ぎます。

### 生成バックエンドの差し替え

`backends/base.py` の `VideoBackend` プロトコルを実装して `backends/__init__.py` に登録すれば
`settings.yaml` の `video.backend` で切り替わります。単価は `pricing.py` に追加してください
（未登録のモデルは 0円ではなくエラーになります。静かに予算を素通りさせないためです）。

| モデル | 8秒1本 |
|---|---|
| `veo-3.1-lite-generate-preview` (720p) | $0.40 ← 既定 |
| `veo-3.1-fast-generate-preview` (1080p) | $0.96 |
| `veo-3.1-generate-preview` (1080p) | $3.20 |
| `gemini-omni-flash` (720p) | $0.80 |

価格表は上限の較正そのものなので、間違っていると実効上限が同じ倍率でずれます。
`doctor` が最終確認日からの経過を警告します。
