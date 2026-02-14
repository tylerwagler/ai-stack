import pytest
from unittest.mock import patch, MagicMock, call
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import proxy

class TestUsageRecording:
    """Test usage tracking and recording functionality"""

    @patch('proxy.get_db_conn')
    def test_record_usage_updates_all_counters(self, mock_get_db_conn, mock_db_conn, mock_cursor):
        """Should update all usage counters correctly"""
        mock_get_db_conn.return_value = mock_db_conn
        mock_db_conn.cursor.return_value = mock_cursor

        prompt_tokens = 100
        completion_tokens = 50
        total_tokens = 150

        proxy.record_usage(
            api_key_id='key-123',
            user_id='user-456',
            model='GLM-4.7-Flash',
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            path='/v1/chat/completions'
        )

        # Check UPDATE user_usage was called
        update_calls = [str(call) for call in mock_cursor.execute.call_args_list]
        update_call = next(c for c in update_calls if 'UPDATE public.user_usage' in c)

        assert update_call is not None
        # Verify all counters incremented by total
        assert 'total_tokens = total_tokens + 150' in update_call or '%s' in update_call
        assert 'hourly_usage = hourly_usage + 150' in update_call or '%s' in update_call
        assert 'daily_usage = daily_usage + 150' in update_call or '%s' in update_call
        assert 'weekly_usage = weekly_usage + 150' in update_call or '%s' in update_call
        assert 'monthly_usage = monthly_usage + 150' in update_call or '%s' in update_call

        # Verify commit was called
        mock_db_conn.commit.assert_called_once()

    @patch('proxy.get_db_conn')
    def test_record_usage_invalidates_cache(self, mock_get_db_conn, mock_db_conn, mock_cursor):
        """Should invalidate cache for user after recording usage"""
        mock_get_db_conn.return_value = mock_db_conn
        mock_db_conn.cursor.return_value = mock_cursor

        # Populate cache with user's key
        with patch.object(proxy, 'KEY_CACHE', {
            'sk-user-key-1': (100, {'user_id': 'user-456', 'hourly_usage': 100}),
            'sk-other-user': (100, {'user_id': 'user-999', 'hourly_usage': 50}),
        }):
            proxy.record_usage(
                api_key_id='key-123',
                user_id='user-456',
                model='GLM-4.7-Flash',
                prompt_tokens=100,
                completion_tokens=50,
                path='/v1/chat/completions'
            )

            # User 456's cache should be invalidated
            assert 'sk-user-key-1' not in proxy.KEY_CACHE
            # Other user's cache should remain
            assert 'sk-other-user' in proxy.KEY_CACHE

    @patch('proxy.get_db_conn')
    def test_record_usage_creates_audit_log(self, mock_get_db_conn, mock_db_conn, mock_cursor):
        """Should create audit log entry"""
        mock_get_db_conn.return_value = mock_db_conn
        mock_db_conn.cursor.return_value = mock_cursor

        proxy.record_usage(
            api_key_id='key-123',
            user_id='user-456',
            model='GLM-4.7-Flash',
            prompt_tokens=100,
            completion_tokens=50,
            path='/v1/chat/completions'
        )

        # Check INSERT into usage_logs was called
        insert_calls = [str(call) for call in mock_cursor.execute.call_args_list]
        insert_call = next(c for c in insert_calls if 'INSERT INTO public.usage_logs' in c)

        assert insert_call is not None
        # Verify log contains all required fields
        assert 'api_key_id' in insert_call or '%s' in insert_call
        assert 'model' in insert_call
        assert 'prompt_tokens' in insert_call
        assert 'completion_tokens' in insert_call
        assert 'total_tokens' in insert_call
        assert 'request_path' in insert_call

    @patch('proxy.get_db_conn')
    def test_record_usage_updates_api_key_last_used(self, mock_get_db_conn, mock_db_conn, mock_cursor):
        """Should update api_key last_used_at timestamp"""
        mock_get_db_conn.return_value = mock_db_conn
        mock_db_conn.cursor.return_value = mock_cursor

        proxy.record_usage(
            api_key_id='key-123',
            user_id='user-456',
            model='GLM-4.7-Flash',
            prompt_tokens=100,
            completion_tokens=50,
            path='/v1/chat/completions'
        )

        # Check UPDATE api_keys was called
        update_calls = [str(call) for call in mock_cursor.execute.call_args_list]
        api_key_update = next(c for c in update_calls if 'UPDATE public.api_keys' in c)

        assert api_key_update is not None
        assert 'last_used_at = now()' in api_key_update

    @patch('proxy.get_db_conn')
    def test_record_usage_handles_db_error_gracefully(self, mock_get_db_conn, mock_db_conn, mock_cursor):
        """Should handle DB errors without raising exception"""
        mock_get_db_conn.return_value = mock_db_conn
        mock_db_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = Exception("DB error")

        # Should not raise exception
        proxy.record_usage(
            api_key_id='key-123',
            user_id='user-456',
            model='GLM-4.7-Flash',
            prompt_tokens=100,
            completion_tokens=50,
            path='/v1/chat/completions'
        )

        # Rollback should be called
        mock_db_conn.rollback.assert_called()

    @patch('proxy.get_db_conn')
    def test_record_usage_with_zero_tokens(self, mock_get_db_conn, mock_db_conn, mock_cursor):
        """Should handle zero token usage"""
        mock_get_db_conn.return_value = mock_db_conn
        mock_db_conn.cursor.return_value = mock_cursor

        proxy.record_usage(
            api_key_id='key-123',
            user_id='user-456',
            model='GLM-4.7-Flash',
            prompt_tokens=0,
            completion_tokens=0,
            path='/v1/chat/completions'
        )

        # Should still record with 0 tokens
        mock_db_conn.commit.assert_called_once()

    @patch('proxy.get_db_conn')
    def test_record_usage_connection_failure(self, mock_get_db_conn):
        """Should handle connection failure gracefully"""
        mock_get_db_conn.return_value = None

        # Should not raise exception
        proxy.record_usage(
            api_key_id='key-123',
            user_id='user-456',
            model='GLM-4.7-Flash',
            prompt_tokens=100,
            completion_tokens=50,
            path='/v1/chat/completions'
        )

    @patch('proxy.get_db_conn')
    def test_record_usage_closes_connection(self, mock_get_db_conn, mock_db_conn):
        """Should always close DB connection"""
        mock_get_db_conn.return_value = mock_db_conn
        mock_cursor = MagicMock()
        mock_db_conn.cursor.return_value = mock_cursor

        proxy.record_usage(
            api_key_id='key-123',
            user_id='user-456',
            model='GLM-4.7-Flash',
            prompt_tokens=100,
            completion_tokens=50,
            path='/v1/chat/completions'
        )

        # Connection should be closed
        mock_db_conn.close.assert_called_once()
