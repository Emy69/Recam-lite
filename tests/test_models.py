from __future__ import annotations

import time

from recam import i18n
from recam.models import LIVE_STATUSES, Status, state_label, status_label

from conftest import make_streamer


def test_key_is_platform_and_lowercased_user():
    assert make_streamer('EmY').key == 'chaturbate:emy'


def test_going_live_reports_a_crossing_and_stamps_the_timeline(streamer):
    before = time.time()
    assert streamer.set_status(Status.ONLINE) is True
    assert streamer.is_live
    assert streamer.live_since >= before
    assert streamer.last_online >= before


def test_staying_live_does_not_report_a_crossing(streamer):
    streamer.set_status(Status.ONLINE)
    first_since = streamer.live_since
    assert streamer.set_status(Status.RECORDING) is False
    assert streamer.live_since == first_since


def test_a_reported_start_wins_over_the_moment_we_noticed(streamer):
    started = time.time() - 3600
    streamer.set_status(Status.ONLINE, started_at=started)
    assert streamer.live_since == started
    assert streamer.last_broadcast_start == started


def test_going_offline_clears_the_live_fields(streamer):
    streamer.set_status(Status.ONLINE)
    streamer.viewers = 120
    assert streamer.set_status(Status.OFFLINE) is True
    assert streamer.live_since == 0.0
    assert streamer.viewers == 0
    assert streamer.last_online > 0


def test_offline_to_unknown_is_not_a_crossing(streamer):
    streamer.set_status(Status.OFFLINE)
    assert streamer.set_status(Status.UNKNOWN) is False


def test_recording_counts_as_live():
    assert set(LIVE_STATUSES) == {Status.ONLINE, Status.RECORDING}
    s = make_streamer()
    s.set_status(Status.RECORDING)
    assert s.is_live


def test_status_labels_follow_the_language():
    i18n.set_language('en')
    assert status_label(Status.ONLINE)[0] == 'LIVE'
    i18n.set_language('es')
    assert status_label(Status.ONLINE)[0] == 'EN VIVO'
    assert status_label(Status.RECORDING)[1] == 'red-8'


def test_state_label_passes_unknown_states_through():
    i18n.set_language('en')
    assert state_label('recording') == 'recording'
    assert state_label('weird') == 'weird'
    i18n.set_language('es')
    assert state_label('processing') == 'procesando'


def test_json_keeps_only_the_persisted_fields(streamer):
    streamer.set_status(Status.RECORDING)
    streamer.last_error = 'boom'
    data = streamer.to_json()
    assert set(data) >= {'url', 'platform', 'username', 'auto_record'}
    assert 'status' not in data
    assert 'last_error' not in data
