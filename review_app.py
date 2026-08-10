"""Human approval gate.

    streamlit run review_app.py

Nothing reaches YouTube without passing through here. That is partly quality
control and partly compliance: YouTube's 2025 inauthentic-content rules ask
channels to demonstrate human editorial involvement, and the reviews table is
the audit trail.

The checks below are the ones no automated stage can make. Rights filtering
reads metadata, and metadata cannot describe what is inside a frame — three
separate test videos shipped a presenter, a NASA slate, and a third-party credit
because of exactly that gap. A person watching for thirty seconds catches all
three.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import streamlit as st  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).parent / ".env")  # same TUBE_AUTO_DB as the CLI

from tube_auto import config, db, paths  # noqa: E402
from tube_auto.budget import month_start  # noqa: E402

st.set_page_config(page_title="tube-auto review", page_icon="🔭", layout="wide")

# What a machine cannot check. Each of these has actually gone wrong.
COMPLIANCE_CHECKS = {
    "no_logo": "NASAのロゴ・スレート・番組タイトルが映っていない",
    "no_person": "特定できる人物が写っていない",
    "no_third_party": "第三者クレジット（ESO / Hubble など）が焼き込まれていない",
    "sources_match": "話している内容が出典と食い違っていない",
    "reading_ok": "固有名詞・数値・単位の読み間違いがない",
}


def _decide(idea_id: int, decision: str, reason: str | None, note: str | None, checks: dict) -> None:
    with db.session() as conn:
        db.insert_review(
            conn, idea_id=idea_id, decision=decision, reason_tag=reason, note=note, checks=checks
        )
        db.set_idea_status(conn, idea_id, "approved" if decision == "approve" else "rejected")
        conn.commit()


def _requeue(idea_id: int) -> None:
    """Send an idea back to be rebuilt from its script.

    The spend ledger is untouched: the money was spent and the budget guard must
    keep seeing it.
    """
    with db.session() as conn:
        db.delete_render(conn, idea_id)
        db.delete_review(conn, idea_id)
        db.set_idea_status(conn, idea_id, "narrated")
        conn.commit()


def _set_title(idea_id: int, title: str) -> None:
    """Titles are metadata only — nothing is burned into the picture, so this
    does not require a re-render."""
    with db.session() as conn:
        db.update_hook(conn, idea_id, title)
        conn.commit()


# --- page --------------------------------------------------------------------

settings = config.load_settings()
reason_tags = settings.get("review", {}).get("reason_tags", [])

st.title("🔭 承認ゲート")

with db.session() as conn:
    pending = db.pending_reviews(conn)
    waiting_to_publish = len(db.publishable_ideas(conn))
    waiting_to_go_live = len(db.private_posts(conn))
    spend = db.spend_breakdown(conn, month_start())
    tts_chars = db.tts_chars_since(conn, month_start())

monthly_limit = float(settings.get("budget", {}).get("monthly_usd", 0.0))
tts_allowance = int(settings.get("tts", {}).get("free_tier_chars_per_month", 1_000_000))
yen = float(settings.get("report", {}).get("jpy_per_usd", 150))

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("レビュー待ち", len(pending))
c2.metric("投稿待ち", waiting_to_publish)
c3.metric("private のまま", waiting_to_go_live)
c4.metric("今月の支出", f"¥{spend['total'] * yen:,.0f}", f"上限 ¥{monthly_limit * yen:,.0f}")
c5.metric("TTS無料枠", f"{tts_chars / tts_allowance:.0%}", f"{tts_chars:,} 文字")

if waiting_to_go_live:
    st.warning(
        f"{waiting_to_go_live} 本が private のままです。`tube-auto go-live` を実行するまで "
        "再生されず、計測も始まりません。"
    )

if not pending:
    st.success("レビュー待ちはありません。`tube-auto build` で1本作ってください。")
    st.stop()

for idea in pending:
    idea_id = int(idea["id"])
    st.divider()

    with db.session() as conn:
        sources = [dict(s) for s in db.get_sources(conn, idea_id)]
        script_row = db.get_script(conn, idea_id)
        assets = [dict(a) for a in db.get_assets(conn, idea_id)]
        narration = db.get_narration(conn, idea_id)

    left, right = st.columns([3, 2])

    with left:
        render_path = idea["render_path"]
        if render_path and Path(render_path).exists():
            st.video(render_path)
        else:
            st.error(f"レンダリング結果が見つかりません: {render_path}")

        minutes = (idea["duration_s"] or 0) / 60
        st.caption(
            f"#{idea_id} · {idea['series_id']} · {minutes:.1f}分 · "
            f"素材 {len(assets)}点 · 出典 {len(sources)}件"
        )

    with right:
        title = st.text_input("タイトル", value=idea["hook"], key=f"title_{idea_id}")
        if (idea["why_now"] or "").strip():
            st.caption(f"なぜ今: {idea['why_now']}")

        st.write("**確認項目**（承認にはすべて必要）")
        checks = {
            key: st.checkbox(label, key=f"chk_{idea_id}_{key}")
            for key, label in COMPLIANCE_CHECKS.items()
        }

        note = st.text_input("メモ（任意）", key=f"note_{idea_id}")
        reason = st.selectbox("却下理由", ["—", *reason_tags], key=f"reason_{idea_id}")

        b1, b2, b3 = st.columns(3)
        if b1.button("✅ 承認", key=f"ok_{idea_id}", type="primary"):
            if not all(checks.values()):
                st.warning("確認項目すべてにチェックを入れてください。")
            else:
                if title.strip() != (idea["hook"] or "").strip():
                    _set_title(idea_id, title.strip())
                _decide(idea_id, "approve", None, note or None, checks)
                st.rerun()

        if b2.button("❌ 却下", key=f"ng_{idea_id}"):
            if reason == "—":
                st.warning("却下理由を選んでください（次回の企画生成にフィードバックされます）")
            else:
                _decide(idea_id, "reject", reason, note or None, checks)
                st.rerun()

        if b3.button("🔄 作り直す", key=f"re_{idea_id}",
                     help="同じ台本と音声から映像を組み直します。追加の課金はありません。"):
            try:
                _requeue(idea_id)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

    with st.expander(f"出典 {len(sources)} 件（説明欄にそのまま載ります）"):
        for source in sources:
            when = (source["published_at"] or "")[:10]
            st.markdown(f"**[{source['ref']}]** {source['title']}  \n{source['url']}  ·  {when}")

    if script_row:
        chapters = json.loads(script_row["chapters_json"])
        with st.expander(f"台本 {script_row['char_count']:,} 文字 / {len(chapters)} 章"):
            for index, chapter in enumerate(chapters):
                st.markdown(f"**{index}. {chapter['title']}**")
                for line in chapter.get("lines", []):
                    refs = " ".join(f"`{r}`" for r in line.get("refs", []))
                    who = "🗣" if line["speaker"] == "explainer" else "❓"
                    st.markdown(f"{who} {line['display']} {refs}")

    with st.expander(f"素材 {len(assets)} 点（権利の記録）"):
        for asset in assets:
            meta = json.loads(asset["meta_json"] or "{}")
            mark = "✅" if asset["license_ok"] else "⚠️"
            st.markdown(
                f"{mark} `{asset['kind']}` ch{asset['chapter']} — "
                f"{meta.get('title', '(生成図解)')}  \n"
                f"{asset['credit'] or '—'}  ·  {asset['source_url'] or 'in-house'}"
            )

st.divider()
st.caption(f"DB: {paths.db_path()}")
