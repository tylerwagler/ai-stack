import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import proxy

class TestAuthentication:
    """Test API key authentication functionality"""

    def test_system_key_bypass(self, mock_env_vars):
        """System key should bypass database lookup"""
        system_key = "sk-test-system-key"
        result = proxy.fetch_api_key_data(system_key)

        assert result is not None
        assert result["system_key"] is True

    @patch('proxy.get_db_conn')
    def test_valid_user_key_with_db(self, mock_get_db_conn, mock_db_conn, mock_cursor, mock_api_key_data):
        """Valid user API key should return key data from database"""
        # Setup mock database responses
        mock_get_db_conn.return_value = mock_db_conn
        mock_db_conn.cursor.return_value = mock_cursor

        # Mock API key lookup
        mock_cursor.fetchone.side_effect = [
            ('key-123', 'user-456', 'Test Key'),  # API key row
            (False, False),  # Subscription check
            (  # Usage row
                100, 500, 2000, 8000, 50000,  # usage counts
                datetime.now(timezone.utc), datetime.now(timezone.utc),
                datetime.now(timezone.utc), datetime.now(timezone.utc),
                1000, 10000, 50000, 200000, 60, 100000  # limits
            )
        ]

        result = proxy.fetch_api_key_data("sk-user-test-key")

        assert result is not None
        assert result["api_key_id"] == "key-123"
        assert result["user_id"] == "user-456"
        assert result["key_name"] == "Test Key"
        assert result["system_key"] is False

    @patch('proxy.get_db_conn')
    def test_invalid_key_returns_none(self, mock_get_db_conn, mock_db_conn, mock_cursor):
        """Invalid API key should return None"""
        mock_get_db_conn.return_value = mock_db_conn
        mock_db_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = None  # No API key found

        result = proxy.fetch_api_key_data("sk-invalid-key")

        assert result is None

    @patch('proxy.get_db_conn')
    def test_cache_hit_behavior(self, mock_get_db_conn):
        """Second call with same key should hit cache, not DB"""
        # First call - DB lookup
        with patch.object(proxy, 'KEY_CACHE', {}):
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_get_db_conn.return_value = mock_conn
            mock_conn.cursor.return_value = mock_cursor

            mock_cursor.fetchone.side_effect = [
                ('key-123', 'user-456', 'Test Key'),
                (False, False),
                (100, 500, 2000, 8000, 50000,
                 datetime.now(timezone.utc), datetime.now(timezone.utc),
                 datetime.now(timezone.utc), datetime.now(timezone.utc),
                 1000, 10000, 50000, 200000, 60, 100000)
            ]

            result1 = proxy.fetch_api_key_data("sk-test-key")

            # Second call - should use cache
            mock_get_db_conn.reset_mock()
            result2 = proxy.fetch_api_key_data("sk-test-key")

            # DB should only be called once
            assert mock_get_db_conn.call_count == 1
            assert result1 == result2

    @patch('proxy.get_db_conn')
    @patch('proxy.time.time')
    def test_cache_expiration(self, mock_time, mock_get_db_conn):
        """Cache should expire after TTL"""
        with patch.object(proxy, 'KEY_CACHE', {}):
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_get_db_conn.return_value = mock_conn
            mock_conn.cursor.return_value = mock_cursor

            mock_cursor.fetchone.side_effect = [
                ('key-123', 'user-456', 'Test Key'),
                (False, False),
                (100, 500, 2000, 8000, 50000,
                 datetime.now(timezone.utc), datetime.now(timezone.utc),
                 datetime.now(timezone.utc), datetime.now(timezone.utc),
                 1000, 10000, 50000, 200000, 60, 100000),
                # Second DB call after expiration
                ('key-123', 'user-456', 'Test Key'),
                (False, False),
                (150, 550, 2050, 8050, 50050,
                 datetime.now(timezone.utc), datetime.now(timezone.utc),
                 datetime.now(timezone.utc), datetime.now(timezone.utc),
                 1000, 10000, 50000, 200000, 60, 100000)
            ]

            # First call at time=0
            mock_time.return_value = 0
            result1 = proxy.fetch_api_key_data("sk-test-key")

            # Second call at time=0 (within TTL) - should hit cache
            mock_get_db_conn.reset_mock()
            result2 = proxy.fetch_api_key_data("sk-test-key")
            assert mock_get_db_conn.call_count == 0

            # Third call at time=31 (after 30s TTL) - should query DB
            mock_time.return_value = 31
            result3 = proxy.fetch_api_key_data("sk-test-key")
            assert mock_get_db_conn.call_count == 1
            assert result3["hourly_usage"] == 150  # New value from DB

    @patch('proxy.get_db_conn')
    def test_db_connection_failure_returns_none(self, mock_get_db_conn):
        """DB connection failure should return None gracefully"""
        mock_get_db_conn.return_value = None

        result = proxy.fetch_api_key_data("sk-test-key")

        assert result is None

    @patch('proxy.get_db_conn')
    def test_missing_usage_row_auto_creation(self, mock_get_db_conn, mock_db_conn, mock_cursor):
        """Missing user_usage row should be auto-created"""
        mock_get_db_conn.return_value = mock_db_conn
        mock_db_conn.cursor.return_value = mock_cursor

        # First fetchone returns API key, second returns None (no usage), third returns usage after insert
        mock_cursor.fetchone.side_effect = [
            ('key-123', 'user-456', 'Test Key'),  # API key
            (False, False),  # Subscription check
            None,  # No usage row initially
            (0, 0, 0, 0, 0,  # Usage row after creation
             datetime.now(timezone.utc), datetime.now(timezone.utc),
             datetime.now(timezone.utc), datetime.now(timezone.utc),
             1000, 10000, 50000, 200000, 60, 100000)
        ]

        result = proxy.fetch_api_key_data("sk-test-key")

        assert result is not None
        # Verify INSERT was called
        assert any('INSERT INTO public.user_usage' in str(call) for call in mock_cursor.execute.call_args_list)
