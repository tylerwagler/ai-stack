import pytest
from unittest.mock import MagicMock, patch
import psycopg2
from datetime import datetime, timedelta, timezone
import sys
import os

# Add parent directory to path to import proxy module
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

@pytest.fixture
def mock_db_conn():
    """Mock PostgreSQL connection"""
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchone.return_value = None
    cursor.fetchall.return_value = []
    return conn

@pytest.fixture
def mock_cursor():
    """Mock database cursor"""
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    cursor.fetchall.return_value = []
    return cursor

@pytest.fixture
def mock_api_key_data():
    """Valid API key data structure"""
    now = datetime.now(timezone.utc)
    return {
        "api_key_id": "key-123",
        "user_id": "user-456",
        "key_name": "Test Key",
        "hourly_usage": 100,
        "daily_usage": 500,
        "weekly_usage": 2000,
        "monthly_usage": 8000,
        "total_tokens": 50000,
        "hourly_reset_at": now + timedelta(minutes=30),
        "daily_reset_at": now + timedelta(hours=12),
        "weekly_reset_at": now + timedelta(days=3),
        "monthly_reset_at": now + timedelta(days=15),
        "hourly_limit": 1000,
        "daily_limit": 10000,
        "weekly_limit": 50000,
        "monthly_limit": 200000,
        "rate_limit_rpm": 60,
        "rate_limit_tpm": 100000,
        "system_key": False
    }

@pytest.fixture
def mock_system_key_data():
    """System API key data"""
    return {"system_key": True}

@pytest.fixture
def mock_llama_models_response():
    """Mock llama-server /v1/models response"""
    return {
        "object": "list",
        "data": [
            {
                "id": "GLM-4.7-Flash",
                "object": "model",
                "created": 1234567890,
                "owned_by": "local",
                "status": {
                    "value": "loaded"
                }
            }
        ]
    }

@pytest.fixture(autouse=True)
def clear_caches():
    """Clear global caches before each test"""
    import proxy
    proxy.KEY_CACHE.clear()
    yield
    # Cleanup after test
    proxy.KEY_CACHE.clear()

@pytest.fixture
def mock_env_vars(monkeypatch):
    """Mock environment variables"""
    monkeypatch.setenv("POSTGRES_DB", "test_db")
    monkeypatch.setenv("POSTGRES_USER", "test_user")
    monkeypatch.setenv("POSTGRES_PASSWORD", "test_pass")
    monkeypatch.setenv("POSTGRES_HOST", "localhost")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
