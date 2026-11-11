from __future__ import annotations

import httpx

from alpha_market_engine.publishers.x_publisher import XPublisher


def test_x_publisher_error_handling(monkeypatch):
    def transport(request):
        return httpx.Response(429, json={"error": "rate limit"})

    client = httpx.Client(transport=httpx.MockTransport(transport))
    pub = XPublisher(client=client)
    monkeypatch.setattr("alpha_market_engine.publishers.x_publisher.get_settings", lambda: type("S", (), {"x_bearer_token": "tok", "x_client_id": "id", "x_client_secret": "secret"})())
    result = pub.publish("test post")
    assert result.ok is False
    assert result.status_code == 429


def test_duplicate_publish_prevention(monkeypatch):
    def transport(request):
        return httpx.Response(201, json={"data": {"id": "123"}})

    client = httpx.Client(transport=httpx.MockTransport(transport))
    pub = XPublisher(client=client)
    monkeypatch.setattr("alpha_market_engine.publishers.x_publisher.get_settings", lambda: type("S", (), {"x_bearer_token": "tok", "x_client_id": "id", "x_client_secret": "secret"})())
    first = pub.publish("test post")
    second = pub.publish("test post")
    assert first.ok is True
    assert second.ok is False
