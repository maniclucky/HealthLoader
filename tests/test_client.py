import pytest

from fitthing.client import ApiError, HealthClient


class FakeResponse:
    def __init__(self, status_code, json_body=None, headers=None, text=""):
        self.status_code = status_code
        self._json_body = json_body or {}
        self.headers = headers or {}
        self.text = text or str(self._json_body)

    def json(self):
        return self._json_body


class FakeSession:
    """Records every call and returns canned responses in order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def request(self, method, url, timeout=None, **kwargs):
        self.calls.append({"method": method, "url": url, "timeout": timeout, **kwargs})
        return self._responses.pop(0)


def make_client(responses, sleep_calls=None):
    session = FakeSession(responses)
    sleeps = sleep_calls if sleep_calls is not None else []
    client = HealthClient(credentials=None, session=session, sleep=lambda s: sleeps.append(s))
    return client, session


def test_pagination_across_three_pages():
    responses = [
        FakeResponse(200, {"dataPoints": [{"id": 1}, {"id": 2}], "nextPageToken": "tok1"}),
        FakeResponse(200, {"dataPoints": [{"id": 3}], "nextPageToken": "tok2"}),
        FakeResponse(200, {"dataPoints": [{"id": 4}]}),  # no nextPageToken: last page
    ]
    client, session = make_client(responses)

    points = list(client.list_data_points("steps", "some filter", page_size=2))

    assert points == [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}]
    assert len(session.calls) == 3
    assert "pageToken" not in session.calls[0]["params"]
    assert session.calls[1]["params"]["pageToken"] == "tok1"
    assert session.calls[2]["params"]["pageToken"] == "tok2"
    assert all(c["params"]["pageSize"] == 2 for c in session.calls)
    assert all(c["params"]["filter"] == "some filter" for c in session.calls)


def test_empty_filter_omitted_from_params():
    responses = [FakeResponse(200, {"dataPoints": []})]
    client, session = make_client(responses)

    list(client.list_data_points("food", "", page_size=10))

    assert "filter" not in session.calls[0]["params"]


def test_retry_on_429_then_success():
    sleeps = []
    responses = [
        FakeResponse(429, {}, headers={}),
        FakeResponse(200, {"dataPoints": [{"id": 1}]}),
    ]
    client, session = make_client(responses, sleeps)

    points = list(client.list_data_points("steps", "f", page_size=10))

    assert points == [{"id": 1}]
    assert len(session.calls) == 2
    assert len(sleeps) == 1
    assert 1.0 <= sleeps[0] <= 1.2  # base 1s + up to 10% jitter


def test_retry_after_header_is_honored_exactly():
    sleeps = []
    responses = [
        FakeResponse(503, {}, headers={"Retry-After": "5"}),
        FakeResponse(200, {"dataPoints": []}),
    ]
    client, session = make_client(responses, sleeps)

    list(client.list_data_points("steps", "f", page_size=10))

    assert sleeps == [5.0]


def test_no_retry_on_403():
    sleeps = []
    responses = [FakeResponse(403, {"error": "nope"}, text='{"error": "nope"}')]
    client, session = make_client(responses, sleeps)

    with pytest.raises(ApiError) as exc:
        list(client.list_data_points("steps", "f", page_size=10))

    assert exc.value.status_code == 403
    assert len(session.calls) == 1
    assert sleeps == []


def test_no_retry_on_400():
    responses = [FakeResponse(400, {"error": "bad"}, text='{"error": "bad"}')]
    client, session = make_client(responses)

    with pytest.raises(ApiError) as exc:
        list(client.list_data_points("steps", "f", page_size=10))

    assert exc.value.status_code == 400
    assert len(session.calls) == 1


def test_exhausts_retries_and_raises():
    sleeps = []
    # 6 total attempts allowed (5 retries); all fail.
    responses = [FakeResponse(500, {}, text="server error") for _ in range(6)]
    client, session = make_client(responses, sleeps)

    with pytest.raises(ApiError) as exc:
        list(client.list_data_points("steps", "f", page_size=10))

    assert exc.value.status_code == 500
    assert len(session.calls) == 6
    assert len(sleeps) == 5
