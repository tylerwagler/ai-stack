import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import proxy


class TestValidateJwtViaGotrue:
    """Test JWT validation via GoTrue."""

    @patch('proxy.requests.get')
    def test_valid_token(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"id": "user-123", "email": "test@example.com"},
        )
        result = proxy.validate_jwt_via_gotrue("valid-token")
        assert result is not None
        assert result["id"] == "user-123"
        assert result["email"] == "test@example.com"

    @patch('proxy.requests.get')
    def test_invalid_token(self, mock_get):
        mock_get.return_value = MagicMock(status_code=401)
        result = proxy.validate_jwt_via_gotrue("bad-token")
        assert result is None

    @patch('proxy.requests.get')
    def test_gotrue_unreachable(self, mock_get):
        mock_get.side_effect = Exception("Connection refused")
        result = proxy.validate_jwt_via_gotrue("some-token")
        assert result is None


class TestAuthLogin:
    """Test POST /v1/auth/login endpoint."""

    def _make_handler(self, body_dict, method="POST"):
        handler = MagicMock()
        handler.command = method
        body = json.dumps(body_dict).encode()
        headers_dict = {"Content-Length": str(len(body)), "Content-Type": "application/json"}
        handler.headers = MagicMock()
        handler.headers.get = lambda k, d=None: headers_dict.get(k, d)
        handler.headers.__getitem__ = lambda s, k: headers_dict.get(k, "")
        handler.headers.__contains__ = lambda s, k: k in headers_dict
        handler.rfile.read.return_value = body
        handler.wfile = MagicMock()
        handler.send_error_json = MagicMock()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        return handler

    @patch('proxy.get_db_conn')
    @patch('proxy.requests.post')
    def test_successful_login(self, mock_post, mock_db):
        handler = self._make_handler({"email": "test@example.com", "password": "pass123"})

        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "access_token": "jwt-token-abc",
                "user": {"id": "user-123", "email": "test@example.com"},
            },
            content=b'',
        )

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [
            (1, "sk_ai_abc123", "test-key", datetime.now(timezone.utc)),
        ]

        proxy.handle_auth_login(handler)

        handler.send_response.assert_called_with(200)
        written = handler.wfile.write.call_args[0][0]
        data = json.loads(written)
        assert data["access_token"] == "jwt-token-abc"
        assert data["user"]["id"] == "user-123"
        assert len(data["api_keys"]) == 1
        assert data["api_keys"][0]["api_key"] == "sk_ai_abc123"

    @patch('proxy.requests.post')
    def test_bad_credentials(self, mock_post):
        handler = self._make_handler({"email": "test@example.com", "password": "wrong"})

        mock_post.return_value = MagicMock(
            status_code=400,
            content=json.dumps({"error": "invalid_grant", "error_description": "Invalid login credentials"}).encode(),
        )

        proxy.handle_auth_login(handler)
        handler.send_response.assert_called_with(400)

    def test_missing_fields(self):
        handler = self._make_handler({"email": "test@example.com"})
        proxy.handle_auth_login(handler)
        handler.send_error_json.assert_called_with(400, "email and password required")

    @patch('proxy.requests.post')
    def test_gotrue_unreachable(self, mock_post):
        handler = self._make_handler({"email": "test@example.com", "password": "pass"})
        mock_post.side_effect = Exception("Connection refused")
        proxy.handle_auth_login(handler)
        handler.send_error_json.assert_called_with(503, "Authentication service unreachable")


class TestAuthKeysGet:
    """Test GET /v1/auth/keys endpoint."""

    def _make_handler(self, token=None):
        handler = MagicMock()
        handler.command = "GET"
        auth_header = f"Bearer {token}" if token else ""
        handler.headers = MagicMock()
        handler.headers.get = lambda k, d="": {"Authorization": auth_header, "Content-Length": "0"}.get(k, d)
        handler.wfile = MagicMock()
        handler.send_error_json = MagicMock()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        return handler

    def test_missing_token(self):
        handler = self._make_handler(token=None)
        proxy.handle_auth_keys_get(handler)
        handler.send_error_json.assert_called_with(401, "Authorization: Bearer <token> required")

    @patch('proxy.validate_jwt_via_gotrue')
    def test_invalid_token(self, mock_validate):
        mock_validate.return_value = None
        handler = self._make_handler(token="bad-token")
        proxy.handle_auth_keys_get(handler)
        handler.send_error_json.assert_called_with(401, "Invalid or expired token")

    @patch('proxy.get_db_conn')
    @patch('proxy.validate_jwt_via_gotrue')
    def test_list_keys(self, mock_validate, mock_db):
        mock_validate.return_value = {"id": "user-123", "email": "test@example.com"}

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        now = datetime.now(timezone.utc)
        mock_cursor.fetchall.return_value = [
            (1, "sk_ai_key1", "key-one", now),
            (2, "sk_ai_key2", "key-two", now),
        ]

        handler = self._make_handler(token="valid-token")
        proxy.handle_auth_keys_get(handler)

        handler.send_response.assert_called_with(200)
        written = handler.wfile.write.call_args[0][0]
        data = json.loads(written)
        assert len(data["keys"]) == 2
        assert data["keys"][0]["api_key"] == "sk_ai_key1"

    @patch('proxy.get_db_conn')
    @patch('proxy.validate_jwt_via_gotrue')
    def test_db_unavailable(self, mock_validate, mock_db):
        mock_validate.return_value = {"id": "user-123"}
        mock_db.return_value = None

        handler = self._make_handler(token="valid-token")
        proxy.handle_auth_keys_get(handler)
        handler.send_error_json.assert_called_with(503, "Database unavailable")


class TestAuthKeysPost:
    """Test POST /v1/auth/keys endpoint."""

    def _make_handler(self, token=None, body_dict=None):
        handler = MagicMock()
        handler.command = "POST"
        auth_header = f"Bearer {token}" if token else ""
        body = json.dumps(body_dict or {}).encode()
        handler.headers = MagicMock()
        handler.headers.get = lambda k, d="": {
            "Authorization": auth_header,
            "Content-Length": str(len(body)),
            "Content-Type": "application/json",
        }.get(k, d)
        handler.rfile.read.return_value = body
        handler.wfile = MagicMock()
        handler.send_error_json = MagicMock()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        return handler

    def test_missing_token(self):
        handler = self._make_handler()
        proxy.handle_auth_keys_post(handler)
        handler.send_error_json.assert_called_with(401, "Authorization: Bearer <token> required")

    @patch('proxy.validate_jwt_via_gotrue')
    def test_invalid_token(self, mock_validate):
        mock_validate.return_value = None
        handler = self._make_handler(token="bad-token")
        proxy.handle_auth_keys_post(handler)
        handler.send_error_json.assert_called_with(401, "Invalid or expired token")

    @patch('proxy.get_db_conn')
    @patch('proxy.validate_jwt_via_gotrue')
    def test_create_key_with_name(self, mock_validate, mock_db):
        mock_validate.return_value = {"id": "user-123"}

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        now = datetime.now(timezone.utc)
        mock_cursor.fetchone.return_value = (42, now)

        handler = self._make_handler(token="valid-token", body_dict={"name": "my-key"})
        proxy.handle_auth_keys_post(handler)

        handler.send_response.assert_called_with(201)
        written = handler.wfile.write.call_args[0][0]
        data = json.loads(written)
        assert data["name"] == "my-key"
        assert data["api_key"].startswith("sk_ai_")
        assert len(data["api_key"]) == 6 + 48  # "sk_ai_" + 48 hex chars
        assert data["id"] == "42"

    @patch('proxy.get_db_conn')
    @patch('proxy.validate_jwt_via_gotrue')
    def test_create_key_default_name(self, mock_validate, mock_db):
        mock_validate.return_value = {"id": "user-123"}

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        now = datetime.now(timezone.utc)
        mock_cursor.fetchone.return_value = (43, now)

        handler = self._make_handler(token="valid-token", body_dict={})
        proxy.handle_auth_keys_post(handler)

        handler.send_response.assert_called_with(201)
        written = handler.wfile.write.call_args[0][0]
        data = json.loads(written)
        assert data["name"].startswith("claude-local-")

    @patch('proxy.get_db_conn')
    @patch('proxy.validate_jwt_via_gotrue')
    def test_db_unavailable(self, mock_validate, mock_db):
        mock_validate.return_value = {"id": "user-123"}
        mock_db.return_value = None

        handler = self._make_handler(token="valid-token")
        proxy.handle_auth_keys_post(handler)
        handler.send_error_json.assert_called_with(503, "Database unavailable")
