import pytest
from unittest.mock import patch, MagicMock
import psycopg2
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import proxy

class TestDatabaseConnections:
    """Test database connection management"""

    @patch('proxy.psycopg2.connect')
    def test_get_db_conn_success(self, mock_connect, mock_env_vars):
        """Should successfully create DB connection"""
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn

        conn = proxy.get_db_conn()

        assert conn is not None
        assert conn == mock_conn
        mock_connect.assert_called_once()

    @patch('proxy.psycopg2.connect')
    def test_get_db_conn_failure(self, mock_connect):
        """Should return None on connection failure"""
        mock_connect.side_effect = psycopg2.OperationalError("Connection failed")

        conn = proxy.get_db_conn()

        assert conn is None

    @patch('proxy.psycopg2.connect')
    def test_get_db_conn_invalid_credentials(self, mock_connect):
        """Should handle invalid credentials gracefully"""
        mock_connect.side_effect = psycopg2.OperationalError("authentication failed")

        conn = proxy.get_db_conn()

        assert conn is None

    def test_release_db_conn_closes_connection(self):
        """Should close the connection"""
        mock_conn = MagicMock()

        proxy.release_db_conn(mock_conn)

        mock_conn.close.assert_called_once()

    def test_release_db_conn_handles_none(self):
        """Should handle None connection gracefully"""
        # Should not raise exception
        proxy.release_db_conn(None)

    def test_release_db_conn_handles_already_closed(self):
        """Should handle already-closed connection gracefully"""
        mock_conn = MagicMock()
        mock_conn.close.side_effect = Exception("Already closed")

        # Should not raise exception
        proxy.release_db_conn(mock_conn)

    @patch('proxy.psycopg2.connect')
    def test_get_db_conn_uses_env_vars(self, mock_connect, mock_env_vars):
        """Should use environment variables for connection"""
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn

        proxy.get_db_conn()

        # Verify psycopg2.connect was called with env vars
        call_kwargs = mock_connect.call_args[1]
        assert call_kwargs['dbname'] == 'test_db'
        assert call_kwargs['user'] == 'test_user'
        assert call_kwargs['password'] == 'test_pass'
        assert call_kwargs['host'] == 'localhost'
        assert call_kwargs['port'] == '5432'
