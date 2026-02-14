import pytest
from unittest.mock import patch, MagicMock
import requests
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import proxy

class TestModelRouting:
    """Test model discovery and routing functionality"""

    @patch('proxy.requests.get')
    def test_get_available_models_success(self, mock_get, mock_llama_models_response):
        """Should successfully fetch available models from llama-server"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = mock_llama_models_response
        mock_get.return_value = mock_response

        models = proxy.get_available_models()

        assert len(models) == 1
        assert models[0]['id'] == 'GLM-4.7-Flash'
        assert models[0]['object'] == 'model'

        # Verify correct endpoint was called
        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert '/v1/models' in call_args[0][0]

    @patch('proxy.requests.get')
    def test_get_available_models_timeout(self, mock_get):
        """Should handle timeout gracefully"""
        mock_get.side_effect = requests.exceptions.Timeout()

        models = proxy.get_available_models()

        assert models == []

    @patch('proxy.requests.get')
    def test_get_available_models_malformed_json(self, mock_get):
        """Should handle malformed JSON response"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("Invalid JSON")
        mock_get.return_value = mock_response

        models = proxy.get_available_models()

        assert models == []

    @patch('proxy.requests.get')
    def test_get_available_models_http_error(self, mock_get):
        """Should handle HTTP errors"""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_get.return_value = mock_response

        models = proxy.get_available_models()

        assert models == []

    @patch('proxy.get_available_models')
    @patch('proxy.time.time')
    def test_get_active_model_cache_hit(self, mock_time, mock_get_available):
        """Should use cache when active model cached"""
        with patch.object(proxy, 'LOADED_MODEL_CACHE', 'GLM-4.7-Flash'):
            with patch.object(proxy, 'LOADED_MODEL_CACHE_TIME', 100):
                mock_time.return_value = 110  # Within 30s TTL

                model = proxy.get_active_model()

                assert model == 'GLM-4.7-Flash'
                # Should NOT call get_available_models due to cache
                mock_get_available.assert_not_called()

    @patch('proxy.get_available_models')
    @patch('proxy.time.time')
    def test_get_active_model_cache_miss(self, mock_time, mock_get_available, mock_llama_models_response):
        """Should query llama-server on cache miss"""
        with patch.object(proxy, 'LOADED_MODEL_CACHE', None):
            with patch.object(proxy, 'LOADED_MODEL_CACHE_TIME', 0):
                mock_time.return_value = 100
                mock_get_available.return_value = mock_llama_models_response['data']

                model = proxy.get_active_model()

                assert model == 'GLM-4.7-Flash'
                mock_get_available.assert_called_once()

    @patch('proxy.get_available_models')
    def test_get_active_model_no_model_loaded(self, mock_get_available):
        """Should return None when no model is loaded"""
        with patch.object(proxy, 'LOADED_MODEL_CACHE', None):
            mock_get_available.return_value = []

            model = proxy.get_active_model()

            assert model is None

    @patch('proxy.get_available_models')
    @patch('proxy.time.time')
    def test_get_active_model_ttl_expiration(self, mock_time, mock_get_available, mock_llama_models_response):
        """Should refresh cache after TTL expires"""
        with patch.object(proxy, 'LOADED_MODEL_CACHE', 'old-model'):
            with patch.object(proxy, 'LOADED_MODEL_CACHE_TIME', 0):
                mock_time.return_value = 35  # Beyond 30s TTL
                mock_get_available.return_value = mock_llama_models_response['data']

                model = proxy.get_active_model()

                assert model == 'GLM-4.7-Flash'
                # Should have called API due to TTL expiration
                mock_get_available.assert_called_once()

    @patch('proxy.get_available_models')
    def test_model_exists_found(self, mock_get_available, mock_llama_models_response):
        """Should return True when model exists"""
        mock_get_available.return_value = mock_llama_models_response['data']

        exists = proxy.model_exists('GLM-4.7-Flash')

        assert exists is True

    @patch('proxy.get_available_models')
    def test_model_exists_not_found(self, mock_get_available, mock_llama_models_response):
        """Should return False when model doesn't exist"""
        mock_get_available.return_value = mock_llama_models_response['data']

        exists = proxy.model_exists('NonExistentModel')

        assert exists is False

    @patch('proxy.get_available_models')
    def test_model_exists_empty_list(self, mock_get_available):
        """Should return False when no models available"""
        mock_get_available.return_value = []

        exists = proxy.model_exists('AnyModel')

        assert exists is False

    def test_get_loaded_model_alias(self):
        """get_loaded_model should be alias for get_active_model"""
        with patch.object(proxy, 'get_active_model') as mock_active:
            mock_active.return_value = 'test-model'

            result = proxy.get_loaded_model()

            assert result == 'test-model'
            mock_active.assert_called_once()
