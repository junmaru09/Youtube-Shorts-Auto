"""Human approval gate.

    streamlit run review_app.py

Nothing reaches YouTube without passing through here. That is partly quality
control and partly compliance: YouTube's 2025 inauthentic-content rules ask
channels to demonstrate human editorial involvement, and this table is the
audit trail. Rejection reasons are fed back into the next `ideate` run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import streamlit as st  # noqa: E402

from shorts_auto import config, db, paths  # noqa: E402
from shorts_auto.stages import postprocess  # noqa: E402

st.set_page_config(page_title="Shorts Review", page_icon="🎬", layout="wide")


def _hook(idea, lang: str) -> str:
    return json.loads(idea["hook_json"]).get(lang, "")


def _renders(conn, idea_id: int) -> dict[str, str]:
    return {
        row["lang"]: row["path"]
        for row in db.assets_for_idea(conn, idea_id)
        if row["lang"] != "src"
    }


def _decide(idea_id: int, decision: str, reason: str | None, note: str | None) -> None:
    with db.session() as conn:
        db.insert_review(
            conn, idea_id=idea_id, decision=decision, reason_tag=reason, note=note
        )
        db.set_idea_status(conn, idea_id, "approved" if decision == "approve" else "rejected")


def _retitle_and_rerender(idea_id: int, hooks: dict[str, str]) -> None:
    """Save edited titles and redo the burn-in so the render matches."""
    with db.session() as conn:
        db.update_hook(conn, idea_id, hooks)
        idea = conn.execute("SELECT * FROM ideas WHERE id = ?", (idea_id,)).fetchone()
        sources = [a for a in db.assets_for_idea(conn, idea_id) if a["lang"] == "src"]
        if not sources:
            st.error("source asset is missing; cannot re-render")
            return
        series = config.series_by_id(idea["series_id"])
        for lang in series.languages:
            db.delete_assets_for_idea(conn, idea_id, lang=lang)
            video, thumb = postprocess.render_asset(sources[-1], idea, lang)
            db.insert_asset(
                conn,
                idea_id=idea_id,
                lang=lang,
                path=str(video),
                backend=sources[-1]["backend"],
                model=sources[-1]["model"],
                duration_s=sources[-1]["duration_s"],
                cost_usd=0.0,
                meta={"thumbnail": str(thumb), "source_asset_id": sources[-1]["id"], "retitled": True},
            )


def _requeue(idea_id: int) -> None:
    """Send an idea back for a fresh generation, keeping the plan."""
    with db.session() as conn:
        db.delete_assets_for_idea(conn, idea_id)
        conn.execute("DELETE FROM reviews WHERE idea_id = ?", (idea_id,))
        db.set_idea_status(conn, idea_id, "ideated")


# --- page --------------------------------------------------------------------

settings = config.load_settings()
reason_tags = settings.get("review", {}).get("reason_tags", [])

st.title("🎬 Shorts 承認ゲート")

with db.session() as conn:
    pending = db.pending_reviews(conn)
    renders = {int(idea["id"]): _renders(conn, int(idea["id"])) for idea in pending}
    approved_waiting = len(db.publishable_assets(conn))

col_a, col_b = st.columns(2)
col_a.metric("レビュー待ち", len(pending))
col_b.metric("投稿待ち（承認済み）", approved_waiting)

if not pending:
    st.success("レビュー待ちはありません。`shorts-auto generate` と `shorts-auto postprocess` を実行してください。")
    st.stop()

for idea in pending:
    idea_id = int(idea["id"])
    st.divider()
    left, right = st.columns([1, 2])

    with left:
        paths_by_lang = renders.get(idea_id, {})
        preview = next(iter(paths_by_lang.values()), None)
        if preview and Path(preview).exists():
            st.video(preview)
        else:
            st.warning("レンダリング結果が見つかりません")

    with right:
        st.caption(f"#{idea_id} · {idea['series_id']}")
        st.write(f"**シーン**: {idea['scene_summary']}")

        edited: dict[str, str] = {}
        for lang in config.series_by_id(idea["series_id"]).languages:
            edited[lang] = st.text_input(
                f"タイトル [{lang}]", value=_hook(idea, lang), key=f"hook_{idea_id}_{lang}"
            )

        with st.expander("生成プロンプト"):
            st.code(idea["video_prompt"], language="text")

        note = st.text_input("メモ（任意）", key=f"note_{idea_id}")
        reason = st.selectbox(
            "却下理由", ["—", *reason_tags], key=f"reason_{idea_id}"
        )

        b1, b2, b3, b4 = st.columns(4)
        if b1.button("✅ 承認", key=f"ok_{idea_id}", type="primary"):
            current = {lang: _hook(idea, lang) for lang in edited}
            if edited != current:
                with st.spinner("タイトルを反映して再レンダリング中…"):
                    _retitle_and_rerender(idea_id, edited)
            _decide(idea_id, "approve", None, note or None)
            st.rerun()

        if b2.button("❌ 却下", key=f"ng_{idea_id}"):
            if reason == "—":
                st.warning("却下理由を選んでください（次回の企画生成にフィードバックされます）")
            else:
                _decide(idea_id, "reject", reason, note or None)
                st.rerun()

        if b3.button("✏️ タイトル反映", key=f"rt_{idea_id}"):
            with st.spinner("再レンダリング中…"):
                _retitle_and_rerender(idea_id, edited)
            st.rerun()

        if b4.button("🔄 再生成", key=f"re_{idea_id}", help="同じ企画で動画を作り直す（課金が発生します）"):
            _requeue(idea_id)
            st.rerun()

st.divider()
st.caption(f"DB: {paths.db_path()}")
