"""Safety net: tests must never reach the network, the real Claude login, or the registry."""
import socket

import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise RuntimeError("tests must not open network connections")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


@pytest.fixture(autouse=True)
def no_real_credit(monkeypatch, tmp_path):
    """The claude-mode credit files live in the real home folder: point them at nothing."""
    from tray import credit
    monkeypatch.setattr(credit, "CREDIT_FILE", tmp_path / "no-credit.json")
    monkeypatch.setattr(credit, "USAGE_FILE", tmp_path / "no-usage.jsonl")
