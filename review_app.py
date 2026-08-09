"""Human approval gate.

    streamlit run review_app.py

Nothing reaches YouTube without passing through here. That is partly quality
control and partly compliance: YouTube's 2025 inauthentic-content rules ask
channels to demonstrate human editorial involvement, and the reviews table is the
audit trail. Rejection reasons feed back into the next `ideate` run.

Two things this page will not do:

- **Delete a spend record.** Regenerating adds a row to the append-only ledger; it
  never removes the one that recorded money already spent.
- **Approve a video whose burned-in title differs from the stored one.** Editing a
  title re-renders first and only saves on success, so the file and the metadata
  cannot drift apart.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import streamlit as st  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).parent / ".env")  # same SHORTS_AUTO_DB as the CLI

from shorts_auto import config, db, ffmpeg, paths  # noqa: E402
from shorts_auto.budget import month_start  # noqa: E402
from shorts_auto.stages import postprocess  # noqa: E402

st.set_page_config(page_title="Shorts Review", page_icon="🎬", layout="wide")

# Checked by a human, recorded as evidence. These are the failure modes no
# prompt or negative prompt reliably prevents.
COMPLIANCE_CHECKS = {
    "no_text": "映像に文字・字幕・透かしが焼き込まれていない",
    "no_real_person": "実在の人物に見える顔が写っていない",
    "no_banned": "シリーズの禁止事項に触れていない",
}


def _decide(idea_id: int, decision: str, reason: str | None, note: str | None, checks: dict) -> None:
    with db.session() as conn:
        db.insert_review(
            conn, idea_id=idea_id, decision=decision, reason_tag=reason, note=note, checks=checks
        )
        db.set_idea_status(conn, idea_id, "approved" if decision == "approve" else "rejected")


def _rerender_with_title(idea_id: int, new_hook: str) -> None:
    """Re-render first, save the title only if it succeeded.

    Saving first and rendering second is how the stored title and the burned-in
    text drift apart, which then ships a video whose caption contradicts its
    metadata.
    """
    with db.session() as conn:
        idea = db.get_idea(conn, idea_id)
        generation = db.latest_generation(conn, idea_id)
        if idea is None or generation is None or not generation["path"]:
            raise RuntimeError("no usable generation on record for this idea")

        candidate = dict(idea)
        candidate["hook"] = new_hook
        video, thumb, burned = postprocess.render_for_idea(candidate, generation)

        db.update_hook(conn, idea_id, new_hook)
        db.upsert_render(
            conn,
            idea_id=idea_id,
            generation_id=int(generation["id"]),
            path=str(video),
            thumb_path=str(thumb) if thumb else None,
            burned_hook=burned,
        )
        conn.commit()


def _requeue(idea_id: int) -> None:
    """Send an idea back for a fresh generation, keeping the plan.

    The previous generation's ledger row stays: the money was spent and the
    budget guard must keep seeing it.
    """
    with db.session() as conn:
        db.delete_render(conn, idea_id)
        db.delete_review(conn, idea_id)
        db.set_idea_status(conn, idea_id, "ideated")
        conn.commit()


# --- page --------------------------------------------------------------------

settings = config.load_settings()
reason_tags = settings.get("review", {}).get("reason_tags", [])

st.title("🎬 Shorts 承認ゲート")

with db.session() as conn:
    pending = db.pending_reviews(conn)
    waiting_to_publish = len(db.publishable_ideas(conn))
    waiting_to_go_live = len(db.private_posts(conn))
    month_spend = db.spend_breakdown(conn, month_start())

monthly_limit = float(settings.get("budget", {}).get("monthly_usd", 0.0))

c1, c2, c3, c4 = st.columns(4)
c1.metric("レビュー待ち", len(pending))
c2.metric("投稿待ち", waiting_to_publish)
c3.metric("private のまま", waiting_to_go_live)
c4.metric("今月の支出", f"${month_spend['total']:.2f}", f"上限 ${monthly_limit:.0f}")

if waiting_to_go_live:
    st.warning(
        f"{waiting_to_go_live} 本が private のままです。`shorts-auto go-live` を実行するまで "
        "再生されず、計測もされません。"
    )

if not pending:
    st.success(
        "レビュー待ちはありません。`shorts-auto ideate` → `generate` → `postprocess` を実行してください。"
    )
    st.stop()

for idea in pending:
    idea_id = int(idea["id"])
    lang = idea["lang"]
    st.divider()
    left, right = st.columns([1, 2])

    with left:
        render_path = idea["render_path"]
        if render_path and Path(render_path).exists():
            st.video(render_path)
        else:
            st.warning(f"レンダリング結果が見つかりません: {render_path}")

    with right:
        st.caption(f"#{idea_id} · {idea['series_id']} · → {lang} チャンネル")
        st.write(f"**シーン**: {idea['scene_summary']}")

        edited = st.text_input(
            f"タイトル [{lang}]", value=idea["hook"], key=f"hook_{idea_id}"
        )
        if idea["burned_hook"]:
            st.caption(f"映像に焼き込まれている文字: {idea['burned_hook']!r}")

        with st.expander("生成プロンプト"):
            st.code(idea["video_prompt"], language="text")

        st.write("**確認項目**（承認にはすべてチェックが必要です）")
        checks = {
            key: st.checkbox(label, key=f"chk_{idea_id}_{key}")
            for key, label in COMPLIANCE_CHECKS.items()
        }

        note = st.text_input("メモ（任意）", key=f"note_{idea_id}")
        reason = st.selectbox("却下理由", ["—", *reason_tags], key=f"reason_{idea_id}")

        b1, b2, b3, b4 = st.columns(4)

        if b1.button("✅ 承認", key=f"ok_{idea_id}", type="primary"):
            if not all(checks.values()):
                st.warning("確認項目すべてにチェックを入れてください。")
            else:
                ok = True
                if edited.strip() != (idea["hook"] or "").strip():
                    with st.spinner("タイトルを反映して再レンダリング中…"):
                        try:
                            _rerender_with_title(idea_id, edited.strip())
                        except (RuntimeError, ffmpeg.FFmpegError, ffmpeg.FFmpegMissing,
                                ffmpeg.FontMissing) as exc:
                            ok = False
                            st.error(f"再レンダリングに失敗したため承認を中止しました: {exc}")
                if ok:
                    _decide(idea_id, "approve", None, note or None, checks)
                    st.rerun()

        if b2.button("❌ 却下", key=f"ng_{idea_id}"):
            if reason == "—":
                st.warning("却下理由を選んでください（次回の企画生成にフィードバックされます）")
            else:
                _decide(idea_id, "reject", reason, note or None, checks)
                st.rerun()

        if b3.button("✏️ タイトル反映", key=f"rt_{idea_id}"):
            with st.spinner("再レンダリング中…"):
                try:
                    _rerender_with_title(idea_id, edited.strip())
                    st.rerun()
                except (RuntimeError, ffmpeg.FFmpegError, ffmpeg.FFmpegMissing,
                        ffmpeg.FontMissing) as exc:
                    st.error(f"再レンダリングに失敗しました: {exc}")

        if b4.button(
            "🔄 再生成",
            key=f"re_{idea_id}",
            help="同じ企画で動画を作り直します。新たに課金が発生し、これまでの支出も台帳に残ります。",
        ):
            try:
                _requeue(idea_id)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

st.divider()
st.caption(f"DB: {paths.db_path()}")
