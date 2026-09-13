from __future__ import annotations

import csv
import io
from datetime import datetime

import streamlit as st

from calscan.settings import Settings
from calscan.store import repo
from calscan.store.db import get_engine, get_session_factory

st.set_page_config(page_title="Journal — Calendar Scanner", page_icon="📓", layout="wide")


@st.cache_resource
def _session_factory() -> object:
    return get_session_factory(get_engine())


settings = Settings.load()
session_factory = _session_factory()

st.title("Journal")
st.caption("Auto-populated on position open/close/hedge, plus free-text notes.")

with st.form(key="add_note"):
    position_id_raw = st.text_input("Position ID (optional)")
    note = st.text_area("Note")
    if st.form_submit_button("Add note") and note:
        position_id = int(position_id_raw) if position_id_raw.strip() else None
        with session_factory() as session:  # type: ignore[operator]
            repo.insert_journal_entry(
                session,
                ts=datetime.utcnow(),
                event_type="note",
                position_id=position_id,
                payload={"note": note},
            )
        st.success("Added.")
        st.rerun()

st.divider()

with session_factory() as session:  # type: ignore[operator]
    entries = repo.get_journal_entries(session)

if not entries:
    st.info("No journal entries yet — entries are recorded automatically when you open, "
             "close, or hedge a position on the Positions page.")
else:
    rows = [
        {
            "ts": e["ts"],
            "event": e["event_type"],
            "position_id": e["position_id"],
            **e["payload"],
        }
        for e in entries
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)

    buffer = io.StringIO()
    fieldnames = sorted({k for row in rows for k in row})
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    st.download_button(
        "Export to CSV",
        data=buffer.getvalue(),
        file_name="journal.csv",
        mime="text/csv",
    )
