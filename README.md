# Youtube-Shorts-Auto

AIショート動画の生成パイプライン。企画 → 生成 → 後処理 → 人間レビュー → 投稿 → 計測 →
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
sudo apt-get install -y ffmpeg fonts-noto-cjk   # 動画後処理とCJKタイトル焼き込みに必須
pip install -e ".[dev,review]"
cp .env.example .env                            # GEMINI_API_KEY と ANTHROPIC_API_KEY を記入
shorts-auto init
```

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
shorts-auto series                 # A/Bテストの各シリーズと現在の配分を表示
shorts-auto ideate --count 3       # 企画を立てる（実績に応じてシリーズを抽選）
shorts-auto generate --limit 3     # Veo で動画生成（予算ガードを通る）
shorts-auto postprocess            # 1080x1920化・音量正規化・タイトル焼き込み・サムネ抽出
streamlit run review_app.py        # ← 人間の承認ゲート。ここを通らないと投稿されない
shorts-auto publish --limit 3      # private でアップロード
shorts-auto sync-stats             # 再生数・視聴維持率を取り込む
shorts-auto report                 # 拡大か中止かを判断するための数字
```

課金せずに動作確認するには:

```bash
shorts-auto generate --dry-run     # fake バックエンド。実価格は表示するが課金しない
shorts-auto publish --dry-run      # 何がどのチャンネルに上がるかだけ表示
```

## 設計上の要点

### 予算の上限はコードで強制する

`config/settings.yaml` の `budget.monthly_usd` / `budget.per_run_usd` を超える生成は
`BudgetExceeded` で拒否されます。`--prompt` の単発生成も同じ台帳に計上されるので、
スモークテストを繰り返して上限をすり抜けることはできません。

### 5つのシリーズ = A/Bテストの腕

`config/series/*.yaml` が1ファイル1ジャンル。**ジャンル追加はファイルを1つ足すだけ**です。

| ID | 内容 | 言語 |
|---|---|---|
| `survival_moment` | 危機一髪・生存本能（参照動画と同型） | ja + en |
| `micro_camera_doc` | マイクロカメラ動物ドキュメンタリー | ja + en |
| `miniature_rescue` | ミニチュア救助（Tiny World Rescue） | ja + en |
| `ai_asmr` | サバイバル/不可能物体系ASMR（飽和したガラス果物系は除外） | ja + en |
| `retro_japan` | 昭和・大正のタイムスリップ映像 | ja のみ |

配分は再生数に比例（`scoring.py`）。ただし各シリーズが10本たまるまでは均等配分のままです —
3本程度の実績で重み付けしても、それは学習ではなくノイズの増幅にしかならないからです。

スコアは**再生数に線形**にしています。Shorts の収益は再生数に線形で、再生数の分布は
ロングテール（大半が数百〜数千、稀に数十万）です。つまり価値があるのは「たまに大当たりを出す」
ジャンルであって「中央値が高い」ジャンルではありません。対数スコアだと、結果を決める信号を
ちょうど潰してしまいます。

### 人間レビューは品質管理であり、同時にコンプライアンス

YouTube の2025年ポリシーは「人間の編集介入の証明」を審査の鍵にしています。`reviews` テーブルが
その監査証跡です。却下理由は次回の `ideate` に負例として渡され、同じ失敗を繰り返さなくなります。

### 重複排除は2層

1. `dedup_key`（正規化ハッシュ + UNIQUE制約）— 再実行や二重実行で同じ企画が入らない
2. Jaccard類似度 — 過去の企画を言い換えただけの案を弾く。閾値は **0.70**（YouTube の
   inauthentic content 判定でよく挙げられる「台本類似度70%未満」に合わせている）

### AI生成であることの開示

投稿時に `status.containsSyntheticMedia = True` を必ず立てます。これは YouTube の
Altered/Synthetic 開示フィールド（2024年10月追加）で、**2026年8月に施行された EU AI Act の
ラベリング義務**にも対応するためのものです。`publish.disclose_synthetic_media` を false に
すると `publish` は起動せずエラーになります。

### ja/en の2チャンネルに同じ素材を流す

同一ファイルを複数チャンネルに上げると再利用コンテンツ判定を受けます。後処理で
**言語ごとに違うタイトルを焼き込む**ため、ja版とen版はバイト単位で別ファイルになります。

### 投稿ペース

YouTube Data API の既定クォータは 10,000 units/日、`videos.insert` が 1,600 units なので
**物理上限は6本/日**。`settings.yaml` はさらに低い3本/日に絞っています。安定した投稿ペースが、
YouTube から見てチャンネルとコンテンツファームを分ける要素だからです。

## テスト

```bash
pytest
```

APIはモックせず呼びません。検証しているのは配分計算・予算ガード・重複判定・
シリーズYAMLのスキーマ・ウィンドウ確定ロジックなど、壊れると静かに損をする部分です。

## 構成

```
config/
  settings.yaml          予算・投稿ペース・モデル・判定閾値
  channels.yaml          ja/en チャンネルとOAuthトークンの場所
  series/*.yaml          A/Bテストの腕（1ファイル1ジャンル）
src/shorts_auto/
  budget.py              月次/実行あたりの上限を強制
  pricing.py             モデル別の秒単価（report の損益分岐計算の根拠）
  dedup.py               重複排除の2層
  scoring.py             実績→シリーズ配分
  report.py              拡大か中止かの判断材料
  ffmpeg.py              1080x1920化・音量正規化・タイトル焼き込み
  youtube.py             Data API / Analytics API
  backends/              VideoBackend プロトコルと Veo 実装（差し替え可能）
  stages/                ideate / generate / postprocess / publish / analytics
review_app.py            Streamlit の承認ゲート
```

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
