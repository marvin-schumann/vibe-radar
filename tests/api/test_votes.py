"""Tests for the city voting endpoints (/api/votes, /api/vote/{city})."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _reset_vote_state():
    """Reset vote counts and IP tracking before each test."""
    from src.api.main import _city_votes, _vote_ips, _VALID_CITIES

    # Reset
    for city in _VALID_CITIES:
        _city_votes[city] = 0
    _vote_ips.clear()
    yield
    # Cleanup
    for city in _VALID_CITIES:
        _city_votes[city] = 0
    _vote_ips.clear()


@pytest.fixture
def client():
    from src.api.main import app

    return TestClient(app)


class TestGetVotes:
    """Tests for GET /api/votes."""

    def test_returns_all_cities(self, client):
        resp = client.get("/api/votes")
        assert resp.status_code == 200
        data = resp.json()
        assert "votes" in data
        assert set(data["votes"].keys()) == {
            "Berlin", "Barcelona", "Amsterdam",
            "London", "Lisbon", "Paris", "Ibiza",
        }

    def test_initial_counts_are_zero(self, client):
        resp = client.get("/api/votes")
        votes = resp.json()["votes"]
        for count in votes.values():
            assert count == 0

    def test_sorted_descending(self, client):
        from src.api.main import _city_votes

        _city_votes["Berlin"] = 10
        _city_votes["Paris"] = 5
        _city_votes["Lisbon"] = 7

        resp = client.get("/api/votes")
        votes = resp.json()["votes"]
        counts = list(votes.values())
        assert counts == sorted(counts, reverse=True)
        assert list(votes.keys())[0] == "Berlin"


class TestVoteForCity:
    """Tests for POST /api/vote/{city}."""

    def test_valid_vote(self, client):
        resp = client.post("/api/vote/Berlin")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["voted"] == "Berlin"
        assert data["votes"]["Berlin"] == 1

    def test_invalid_city_rejected(self, client):
        resp = client.post("/api/vote/Tokyo")
        assert resp.status_code == 400
        assert "invalid city" in resp.json()["error"]

    def test_rate_limit_same_ip(self, client):
        # First vote succeeds
        resp1 = client.post("/api/vote/Berlin")
        assert resp1.status_code == 200

        # Second vote from same IP fails
        resp2 = client.post("/api/vote/Barcelona")
        assert resp2.status_code == 429
        assert "already voted" in resp2.json()["error"]

    def test_rate_limit_expires(self, client):
        from src.api.main import _vote_ips

        # First vote succeeds
        resp1 = client.post("/api/vote/Berlin")
        assert resp1.status_code == 200

        # Manually expire the rate limit
        for ip in _vote_ips:
            _vote_ips[ip] = time.time() - 86401

        # Now a second vote should succeed
        resp2 = client.post("/api/vote/Barcelona")
        assert resp2.status_code == 200
        assert resp2.json()["voted"] == "Barcelona"

    def test_vote_increments_count(self, client):
        from src.api.main import _vote_ips

        client.post("/api/vote/Paris")
        _vote_ips.clear()  # allow another vote
        client.post("/api/vote/Paris")

        resp = client.get("/api/votes")
        assert resp.json()["votes"]["Paris"] == 2

    def test_returns_all_votes_after_voting(self, client):
        resp = client.post("/api/vote/Amsterdam")
        data = resp.json()
        assert len(data["votes"]) == 7  # all 7 cities
