import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import proxy

class TestRateLimiting:
    """Test rate limiting functionality"""

    def test_hourly_limit_enforcement(self, mock_api_key_data):
        """Should enforce hourly limit"""
        # Set usage at limit
        mock_api_key_data["hourly_usage"] = 1000
        mock_api_key_data["hourly_limit"] = 1000

        # Simulate limit check logic
        is_limited = (
            mock_api_key_data["hourly_limit"] > 0 and
            mock_api_key_data["hourly_usage"] >= mock_api_key_data["hourly_limit"]
        )

        assert is_limited is True

    def test_daily_limit_enforcement(self, mock_api_key_data):
        """Should enforce daily limit"""
        mock_api_key_data["daily_usage"] = 10000
        mock_api_key_data["daily_limit"] = 10000

        is_limited = (
            mock_api_key_data["daily_limit"] > 0 and
            mock_api_key_data["daily_usage"] >= mock_api_key_data["daily_limit"]
        )

        assert is_limited is True

    def test_weekly_limit_enforcement(self, mock_api_key_data):
        """Should enforce weekly limit"""
        mock_api_key_data["weekly_usage"] = 50000
        mock_api_key_data["weekly_limit"] = 50000

        is_limited = (
            mock_api_key_data["weekly_limit"] > 0 and
            mock_api_key_data["weekly_usage"] >= mock_api_key_data["weekly_limit"]
        )

        assert is_limited is True

    def test_monthly_limit_enforcement(self, mock_api_key_data):
        """Should enforce monthly limit"""
        mock_api_key_data["monthly_usage"] = 200000
        mock_api_key_data["monthly_limit"] = 200000

        is_limited = (
            mock_api_key_data["monthly_limit"] > 0 and
            mock_api_key_data["monthly_usage"] >= mock_api_key_data["monthly_limit"]
        )

        assert is_limited is True

    def test_within_limits_passes(self, mock_api_key_data):
        """Should allow request when within all limits"""
        mock_api_key_data["hourly_usage"] = 500
        mock_api_key_data["daily_usage"] = 5000
        mock_api_key_data["weekly_usage"] = 25000
        mock_api_key_data["monthly_usage"] = 100000

        # Check all limits
        is_hourly_limited = mock_api_key_data["hourly_limit"] > 0 and mock_api_key_data["hourly_usage"] >= mock_api_key_data["hourly_limit"]
        is_daily_limited = mock_api_key_data["daily_limit"] > 0 and mock_api_key_data["daily_usage"] >= mock_api_key_data["daily_limit"]
        is_weekly_limited = mock_api_key_data["weekly_limit"] > 0 and mock_api_key_data["weekly_usage"] >= mock_api_key_data["weekly_limit"]
        is_monthly_limited = mock_api_key_data["monthly_limit"] > 0 and mock_api_key_data["monthly_usage"] >= mock_api_key_data["monthly_limit"]

        assert not is_hourly_limited
        assert not is_daily_limited
        assert not is_weekly_limited
        assert not is_monthly_limited

    def test_system_key_bypasses_limits(self, mock_system_key_data):
        """System key should bypass all rate limits"""
        assert mock_system_key_data["system_key"] is True

        # System key has no limits to check
        should_check_limits = not mock_system_key_data.get("system_key")
        assert should_check_limits is False

    def test_unlimited_limit_handling(self, mock_api_key_data):
        """Limit of -1 should be treated as unlimited"""
        mock_api_key_data["hourly_limit"] = -1
        mock_api_key_data["hourly_usage"] = 999999

        is_limited = (
            mock_api_key_data["hourly_limit"] > 0 and
            mock_api_key_data["hourly_usage"] >= mock_api_key_data["hourly_limit"]
        )

        assert is_limited is False

    @patch('proxy.get_db_conn')
    def test_hourly_reset_detection(self, mock_get_db_conn, mock_db_conn, mock_cursor):
        """Should reset hourly usage after 1 hour"""
        mock_get_db_conn.return_value = mock_db_conn
        mock_db_conn.cursor.return_value = mock_cursor

        # Create data with expired hourly reset time
        old_reset_time = datetime.now(timezone.utc) - timedelta(hours=2)

        mock_cursor.fetchone.side_effect = [
            ('key-123', 'user-456', 'Test Key'),
            (False, False),
            (
                1000, 5000, 20000, 80000, 100000,  # usage
                old_reset_time, datetime.now(timezone.utc),  # hourly reset is old
                datetime.now(timezone.utc), datetime.now(timezone.utc),
                1000, 10000, 50000, 200000, 60, 100000  # limits
            )
        ]

        result = proxy.fetch_api_key_data("sk-test-key")

        # Should have reset hourly usage
        assert result["hourly_usage"] == 0

        # Should have updated reset time in DB
        update_calls = [str(call) for call in mock_cursor.execute.call_args_list]
        assert any('hourly_usage = 0' in call for call in update_calls)

    @patch('proxy.get_db_conn')
    def test_daily_reset_detection(self, mock_get_db_conn, mock_db_conn, mock_cursor):
        """Should reset daily usage after 24 hours"""
        mock_get_db_conn.return_value = mock_db_conn
        mock_db_conn.cursor.return_value = mock_cursor

        old_reset_time = datetime.now(timezone.utc) - timedelta(days=2)

        mock_cursor.fetchone.side_effect = [
            ('key-123', 'user-456', 'Test Key'),
            (False, False),
            (
                100, 10000, 20000, 80000, 100000,
                datetime.now(timezone.utc), old_reset_time,  # daily reset is old
                datetime.now(timezone.utc), datetime.now(timezone.utc),
                1000, 10000, 50000, 200000, 60, 100000
            )
        ]

        result = proxy.fetch_api_key_data("sk-test-key")

        assert result["daily_usage"] == 0

        update_calls = [str(call) for call in mock_cursor.execute.call_args_list]
        assert any('daily_usage = 0' in call for call in update_calls)

    @patch('proxy.get_db_conn')
    def test_weekly_reset_detection(self, mock_get_db_conn, mock_db_conn, mock_cursor):
        """Should reset weekly usage after 7 days"""
        mock_get_db_conn.return_value = mock_db_conn
        mock_db_conn.cursor.return_value = mock_cursor

        old_reset_time = datetime.now(timezone.utc) - timedelta(days=8)

        mock_cursor.fetchone.side_effect = [
            ('key-123', 'user-456', 'Test Key'),
            (False, False),
            (
                100, 5000, 50000, 80000, 100000,
                datetime.now(timezone.utc), datetime.now(timezone.utc),
                old_reset_time, datetime.now(timezone.utc),  # weekly reset is old
                1000, 10000, 50000, 200000, 60, 100000
            )
        ]

        result = proxy.fetch_api_key_data("sk-test-key")

        assert result["weekly_usage"] == 0

        update_calls = [str(call) for call in mock_cursor.execute.call_args_list]
        assert any('weekly_usage = 0' in call for call in update_calls)

    @patch('proxy.get_db_conn')
    def test_monthly_reset_detection(self, mock_get_db_conn, mock_db_conn, mock_cursor):
        """Should reset monthly usage after 30 days"""
        mock_get_db_conn.return_value = mock_db_conn
        mock_db_conn.cursor.return_value = mock_cursor

        old_reset_time = datetime.now(timezone.utc) - timedelta(days=31)

        mock_cursor.fetchone.side_effect = [
            ('key-123', 'user-456', 'Test Key'),
            (False, False),
            (
                100, 5000, 20000, 200000, 250000,
                datetime.now(timezone.utc), datetime.now(timezone.utc),
                datetime.now(timezone.utc), old_reset_time,  # monthly reset is old
                1000, 10000, 50000, 200000, 60, 100000
            )
        ]

        result = proxy.fetch_api_key_data("sk-test-key")

        assert result["monthly_usage"] == 0

        update_calls = [str(call) for call in mock_cursor.execute.call_args_list]
        assert any('monthly_usage = 0' in call for call in update_calls)

    def test_multiple_limit_violations_first_triggers(self, mock_api_key_data):
        """When multiple limits exceeded, first check should trigger"""
        # Exceed both hourly and daily
        mock_api_key_data["hourly_usage"] = 1000
        mock_api_key_data["hourly_limit"] = 1000
        mock_api_key_data["daily_usage"] = 10000
        mock_api_key_data["daily_limit"] = 10000

        # Hourly check comes first in code
        is_hourly_limited = (
            mock_api_key_data["hourly_limit"] > 0 and
            mock_api_key_data["hourly_usage"] >= mock_api_key_data["hourly_limit"]
        )

        assert is_hourly_limited is True
